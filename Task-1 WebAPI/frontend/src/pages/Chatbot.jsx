import { useState, useRef, useEffect, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { authHeader, handleUnauthorized } from "../auth"

const API = "http://127.0.0.1:8000"

function Chatbot() {
  const navigate = useNavigate()
  const role = localStorage.getItem("userRole") || "user"
  const email = localStorage.getItem("userEmail") || "user@novac.com"

  /* ui state */
  const [messages, setMessages] = useState([])
  const [selectedFile, setSelectedFile] = useState(null)
  const [documentName, setDocumentName] = useState("")
  const [query, setQuery] = useState("")
  const [isRecording, setIsRecording] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isThinking, setIsThinking] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [llmModel, setLlmModel] = useState(localStorage.getItem("llmModel") || "mistral")
  const [showToast, setShowToast] = useState(false)
  const [playedMessageIndex, setPlayedMessageIndex] = useState(null)
  const audioRef = useRef(null)
  const chatEndRef = useRef(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, isThinking])

  /* ---------------- upload ---------------- */
  const handleUpload = async () => {
    if (!selectedFile) return alert("Please select a file")
    if (!documentName.trim()) return alert("Please enter a document name")

    const formData = new FormData()
    formData.append("file", selectedFile)
    formData.append("document_name", documentName)
    formData.append("model", llmModel)

    setIsUploading(true)
    try {
      const response = await fetch(`${API}/upload`, {
        method: "POST",
        headers: { ...authHeader() },
        body: formData,
      })
      if (handleUnauthorized(response)) return
      if (!response.ok) {
        const detail = await response.text().catch(() => "")
        alert(`Upload failed (${response.status}). ${detail.slice(0, 300)}`)
        return
      }
      await response.json().catch(() => ({}))
      setShowToast(true)
      setTimeout(() => setShowToast(false), 3000)
      const uploadedName = documentName
      setDocumentName("")
      setSelectedFile(null)
      if (messages.length === 0) {
        setMessages([
          { type: "bot", text: `Got it — "${uploadedName}" is indexed. Ask me anything about it! 📚` },
        ])
      }
    } catch {
      alert("Upload failed")
    } finally {
      setIsUploading(false)
    }
  }

  /* ---------------- voice (TTS) ---------------- */
  const speakText = async (text, index) => {
    try {
      if (audioRef.current && isPlaying && playedMessageIndex === index) {
        audioRef.current.pause()
        setIsPlaying(false)
        return
      }
      if (audioRef.current && !isPlaying && playedMessageIndex === index) {
        await audioRef.current.play()
        setIsPlaying(true)
        return
      }
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current.currentTime = 0
        audioRef.current = null
      }
      const response = await fetch(`${API}/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: JSON.stringify({ query: text }),
      })
      if (handleUnauthorized(response)) return
      const audioBlob = await response.blob()
      const audio = new Audio(URL.createObjectURL(audioBlob))
      audioRef.current = audio
      setIsPlaying(true)
      setPlayedMessageIndex(index)
      audio.onended = () => {
        setIsPlaying(false)
        setPlayedMessageIndex(null)
      }
      await audio.play()
    } catch (e) {
      console.log(e)
      setIsPlaying(false)
      alert("Voice playback failed")
    }
  }

  const handleClearChat = () => {
    setMessages([])
  }

  const changeModel = (e) => {
    setLlmModel(e.target.value)
    localStorage.setItem("llmModel", e.target.value)
  }

  const replay = (text, index) => {
    if (audioRef.current && playedMessageIndex === index) {
      audioRef.current.currentTime = 0
      audioRef.current.play()
      setIsPlaying(true)
    } else {
      speakText(text, index)
    }
  }

  /* ---------------- search ---------------- */
  const handleSearch = async (override) => {
    const q = (typeof override === "string" ? override : query).trim()
    if (!q) return
    if (q.length > 500) return alert("Prompt is too long. Please limit to 500 characters.")
    setQuery("")
    setIsThinking(true)
    const base = [...messages, { type: "user", text: q }]
    setMessages(base)

    try {
      const response = await fetch(`${API}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: JSON.stringify({ query: q, model: llmModel })
      })
      if (handleUnauthorized(response)) return
      const data = await response.json()
      const chunksUsed = data.chunks_used || 0
      setMessages([
        ...base,
        {
          type: "bot",
          text: data.response,
          detectedLanguage: data.detected_language,
          chunks: chunksUsed,
          sources: data.sources || [],
          isTyping: true,
        },
      ])
    } catch {
      setMessages([
        ...base,
        { type: "bot", text: "⚠️ Couldn't reach the server. Please try again." },
      ])
    } finally {
      setIsThinking(false)
    }
  }

  /* ---------------- voice (STT) ---------------- */
  const handleVoice = async () => {
    if (isRecording) return
    try {
      setIsRecording(true)
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream)
      const audioChunks = []
      mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data)
      mediaRecorder.start()
      setTimeout(() => mediaRecorder.stop(), 5000)
      mediaRecorder.onstop = async () => {
        // Release the microphone so the browser's "recording" indicator turns off.
        stream.getTracks().forEach((t) => t.stop())
        try {
          const blob = new Blob(audioChunks, { type: "audio/wav" })
          const formData = new FormData()
          formData.append("file", blob, "voice.wav")
          const response = await fetch(`${API}/voice`, {
            method: "POST",
            headers: { ...authHeader() },
            body: formData,
          })
          if (handleUnauthorized(response)) return
          const data = await response.json()
          setQuery(data.text)
        } catch {
          alert("Voice transcription failed")
        } finally {
          setIsRecording(false)
        }
      }
    } catch (e) {
      console.log(e)
      alert("Microphone access failed")
      setIsRecording(false)
    }
  }

  // Mark a message's typewriter as finished (re-renders so its meta/actions show)
  const markTyped = (idx) =>
    setMessages((prev) => prev.map((mm, i) => (i === idx ? { ...mm, isTyping: false } : mm)))

  return (
    <div className="app-shell" style={{ display: "flex", justifyContent: "center", backgroundColor: "#111827", minHeight: "100vh" }}>                                                                       {/* ---------------- Main ---------------- */}
      <div className="main-col" style={{ maxWidth: "800px", width: "100%", margin: "0 auto", position: "relative", padding: "40px 20px" }}>                                                                          
        <div style={{ position: "absolute", top: "10px", right: "20px", display: "flex", alignItems: "center", gap: "20px", zIndex: 10 }}>                                                                
            <div className="user-meta" style={{ display: "flex", alignItems: "center", gap: "10px" }}>                                                                                          
                <div className="user-chip" style={{ width: "32px", height: "32px", backgroundColor: "#3b82f6", color: "white", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: "bold" }}>{email[0]?.toUpperCase()}</div>                 
                <div>
                   <div className="um-email" style={{ fontSize: "14px", fontWeight: "500", color: "#f3f4f6" }}>{email}</div>                                                                         
                   <div className="um-role" style={{ fontSize: "12px", color: "#9ca3af", textTransform: "capitalize" }}>{role}</div>                                                               
                </div>
            </div>
            <button className="logout-btn" style={{ padding: "8px 16px", border: "1px solid #374151", borderRadius: "8px", backgroundColor: "#1f2937", color: "#f3f4f6", cursor: "pointer", fontSize: "14px", fontWeight: "500", transition: "all 0.2s ease" }} onClick={() => { localStorage.removeItem("token"); localStorage.removeItem("isAuthenticated"); navigate("/") }} onMouseOver={(e) => e.currentTarget.style.backgroundColor = "#374151"} onMouseOut={(e) => e.currentTarget.style.backgroundColor = "#1f2937"}>                                                                      
              Logout
            </button>
        </div>

        <div className="topbar" style={{ display: "flex", alignItems: "center", gap: "15px", paddingBottom: "20px", borderBottom: "1px solid #374151", marginBottom: "30px", marginTop: "20px" }}>                                                                         
          <h1 style={{margin: 0, fontSize: "28px", color: "#f9fafb" }}>NOVAC AI Chatbot</h1>
          {role === "admin" && <span className="pill" style={{backgroundColor: "#3b82f6", color: "white", padding: "4px 10px", borderRadius: "20px", fontSize: "12px", fontWeight: "600", letterSpacing: "0.5px"}}>ADMIN</span>}
          <div className="spacer" style={{flex: 1}} />
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "13px", color: "#9ca3af" }}>🧠 Model</span>
            <select
              value={llmModel}
              onChange={changeModel}
              title="Choose the LLM used for everything — document chunking on upload and chat answers"
              style={{ background: "#1f2937", border: "1px solid #374151", color: "#f3f4f6", fontSize: "14px", fontWeight: "500", padding: "8px 12px", borderRadius: "8px", cursor: "pointer", outline: "none" }}
            >
              <option value="mistral">Mistral (small-latest)</option>
              <option value="groq">Groq (Llama 3.3 70B)</option>
              <option value="grok">Grok (xAI)</option>
            </select>
          </div>
          {role === "admin" && (
            <button className="ghost-btn" style={{ background: "#1f2937", border: "1px solid #374151", color: "#f3f4f6", fontSize: "14px", fontWeight: "500", padding: "8px 16px", borderRadius: "8px", cursor: "pointer", transition: "all 0.2s" }} onMouseOver={(e) => e.currentTarget.style.backgroundColor = "#374151"} onMouseOut={(e) => e.currentTarget.style.backgroundColor = "#1f2937"} onClick={() => navigate("/chunks")}>🗂 Manage Chunks</button>                                                                                      
          )}                                                                                       
          <button className="ghost-btn danger" style={{ background: "#1f2937", border: "1px solid #7f1d1d", color: "#fca5a5", fontSize: "14px", fontWeight: "500", padding: "8px 16px", borderRadius: "8px", cursor: "pointer", transition: "all 0.2s" }} onMouseOver={(e) => e.currentTarget.style.backgroundColor = "#7f1d1d"} onMouseOut={(e) => e.currentTarget.style.backgroundColor = "#1f2937"} onClick={handleClearChat}>🗑 Clear</button>
        </div>

        {role === "admin" && (
          <div className="upload-panel" style={{ backgroundColor: "#1f2937", padding: "20px", borderRadius: "10px", marginBottom: "20px", boxShadow: "0 4px 6px rgba(0,0,0,0.3)" }}>
            <h3 style={{marginTop: 0, color: "#f9fafb"}}>📤 Upload Document</h3>
            <div className="upload-row" style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              <input
                className="field"
                style={{ flex: 1, padding: "10px 16px", borderRadius: "8px", border: "1px solid #374151", outline: "none", fontSize: "14px", backgroundColor: "#111827", color: "white" }}
                placeholder="Document name"
                value={documentName}
                onChange={(e) => setDocumentName(e.target.value)}
              />
              <label className="file-pill" style={{ cursor: "pointer", padding: "10px 16px", backgroundColor: "#374151", borderRadius: "8px", border: "1px dashed #6b7280", fontSize: "14px", color: "#e5e7eb", transition: "all 0.2s" }} onMouseOver={(e) => e.currentTarget.style.backgroundColor = "#4b5563"} onMouseOut={(e) => e.currentTarget.style.backgroundColor = "#374151"}>
                {selectedFile ? `📄 ${selectedFile.name}` : "Choose file…"}
                <input type="file" hidden onChange={(e) => setSelectedFile(e.target.files[0])} />                                                                                               
              </label>
              <button className="btn-primary" disabled={isUploading} style={{ padding: "10px 24px", backgroundColor: isUploading ? "#374151" : "#3b82f6", color: "white", border: "none", borderRadius: "8px", cursor: isUploading ? "not-allowed" : "pointer", fontSize: "14px", fontWeight: "500", transition: "background 0.2s", display: "flex", alignItems: "center", gap: "8px", minWidth: "110px", justifyContent: "center" }} onMouseOver={(e) => !isUploading && (e.currentTarget.style.backgroundColor = "#2563eb")} onMouseOut={(e) => !isUploading && (e.currentTarget.style.backgroundColor = "#3b82f6")} onClick={handleUpload}>
                {isUploading ? (
                  <>
                    <span style={{ width: "14px", height: "14px", border: "2px solid rgba(255,255,255,0.4)", borderTopColor: "white", borderRadius: "50%", display: "inline-block", animation: "spin 0.7s linear infinite" }} />
                    Uploading…
                  </>
                ) : "Upload"}
              </button>
            </div>
          </div>
        )}

        <div className="chat-area" style={{ backgroundColor: "#1f2937", borderRadius: "10px", padding: "20px", minHeight: "400px", boxShadow: "0 4px 6px rgba(0,0,0,0.3)", display: "flex", flexDirection: "column" }}>
          <div className="chat-inner" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
            {messages.length === 0 ? (
              <div className="welcome" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", flex: 1, color: "#9ca3af" }}>
                <div className="welcome-emoji" style={{ fontSize: "40px", marginBottom: "10px" }}>👋</div>
                <div className="welcome-title" style={{ fontSize: "24px", fontWeight: "bold", color: "#f9fafb", marginBottom: "5px" }}>Welcome to NOVAC AI</div>
                <div className="welcome-sub" style={{ marginBottom: "30px" }}>Multilingual Enterprise RAG Assistant</div>                                                              
              </div>
            ) : (
              messages.map((m, index) =>
                m.type === "user" ? (
                  <div className="msg-row user" style={{ display: "flex", flexDirection: "row", justifyContent: "flex-end", margin: "10px 0" }} key={index}>
                    <div className="bubble user" style={{ backgroundColor: "#3b82f6", color: "white", padding: "12px 16px", borderRadius: "18px 18px 0px 18px", maxWidth: "70%" }}>{m.text}</div>
                    <div className="avatar user" style={{ fontSize: "24px", marginLeft: "10px" }}>🧑</div>
                  </div>
                ) : (
                  <div className="msg-row bot" style={{ display: "flex", justifyContent: "flex-start", margin: "10px 0" }} key={index}>
                    <div className="avatar bot" style={{ fontSize: "24px", marginRight: "10px" }}>🤖</div>
                    <div className="bubble bot" style={{ backgroundColor: "#374151", color: "#f3f4f6", padding: "12px 16px", borderRadius: "18px 18px 18px 0px", maxWidth: "70%" }}>
                      <TypewriterText
                        text={m.text}
                        active={!!m.isTyping}
                        onComplete={() => markTyped(index)}
                      />
                      {!m.isTyping && (
                        <BotMeta
                          message={m}
                          isSpeaking={isPlaying && playedMessageIndex === index}
                          onSpeak={() => speakText(m.text, index)}
                          onReplay={() => replay(m.text, index)}
                        />
                      )}
                    </div>
                  </div>
                )
              )
            )}

            {isThinking && (
              <div className="thinking-row" style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "15px", color: "#9ca3af" }}>
                <div className="avatar bot" style={{ fontSize: "24px" }}>🤖</div>
                <div className="thinking-bubble" style={{ backgroundColor: "#374151", color: "#f3f4f6", padding: "12px 16px", borderRadius: "18px", fontStyle: "italic" }}>
                  <span className="label">Thinking...</span>
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
        </div>

        {/* ---------------- Composer ---------------- */}
        <div className="composer-wrap" style={{ marginTop: "20px" }}>
          <div className="composer" style={{ display: "flex", alignItems: "center", gap: "10px", backgroundColor: "#1f2937", padding: "10px", borderRadius: "30px", boxShadow: "0 4px 6px rgba(0,0,0,0.3)", border: "1px solid #374151" }}>
            <input
              style={{ flex: 1, border: "none", outline: "none", padding: "10px", fontSize: "16px", backgroundColor: "transparent", color: "white" }}
              placeholder="Ask anything about your documents…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isThinking) { e.preventDefault(); handleSearch() }                                                                                                    
              }}
            />
            <button
              style={{ width: "40px", height: "40px", borderRadius: "50%", border: "none", backgroundColor: isThinking ? "#374151" : (isRecording ? "#ef4444" : "#374151"), color: isRecording ? "white" : "#9ca3af", cursor: isThinking ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "18px", transition: "all 0.2s" }}
              onClick={handleVoice}
              disabled={isThinking}
              title="Record voice (5s)"
              onMouseOver={(e) => !isThinking && (e.currentTarget.style.backgroundColor = isRecording ? "#dc2626" : "#4b5563")}
              onMouseOut={(e) => !isThinking && (e.currentTarget.style.backgroundColor = isRecording ? "#ef4444" : "#374151")}
            >
              🎤
            </button>
            <button
              style={{ width: "40px", height: "40px", borderRadius: "50%", border: "none", backgroundColor: isThinking || query.trim().length === 0 ? "#374151" : "#3b82f6", color: isThinking || query.trim().length === 0 ? "#6b7280" : "white", cursor: isThinking || query.trim().length === 0 ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "18px", transition: "background 0.2s" }}
              onClick={() => handleSearch()}
              disabled={isThinking || query.trim().length === 0}
              title="Send"
              onMouseOver={(e) => !(isThinking || query.trim().length === 0) && (e.currentTarget.style.backgroundColor = "#2563eb")}
              onMouseOut={(e) => !(isThinking || query.trim().length === 0) && (e.currentTarget.style.backgroundColor = "#3b82f6")}
            >
              ➤
            </button>
          </div>
        </div>
      </div>

      {showToast && (
        <div style={{ position: "fixed", top: "auto", bottom: "20px", right: "20px", backgroundColor: "#10b981", color: "white", padding: "12px 20px", borderRadius: "8px", boxShadow: "0 4px 6px rgba(0,0,0,0.1)", display: "flex", alignItems: "center", gap: "8px", fontSize: "14px" }}>
            <span>✅</span> Document uploaded successfully
        </div>
      )}
    </div>
  )
}

/* ---------- bot message meta: language badge, sources, actions ---------- */
function BotMeta({ message, isSpeaking, onSpeak, onReplay }) {
  const [openSources, setOpenSources] = useState(false)
  const sources = message.sources || []

  return (
    <div className="bubble-meta" style={{ marginTop: "10px", fontSize: "12px", color: "#9ca3af" }}>
      {message.detectedLanguage && (
        <div className="lang-badge" style={{ color: "#60a5fa", fontWeight: "bold", marginBottom: "8px" }}>🌐 Detected Language: {message.detectedLanguage}</div>
      )}

      {sources.length > 0 && (
        <div style={{ marginBottom: "8px" }}>
          <button
            style={{ background: "none", border: "none", color: "#9ca3af", cursor: "pointer", display: "flex", alignItems: "center", gap: "5px", padding: 0 }}
            onClick={() => setOpenSources((v) => !v)}
          >
            <span style={{ transform: openSources ? "rotate(90deg)" : "none", transition: "transform 0.2s" }}>▶</span> {sources.length} source{sources.length > 1 ? "s" : ""}                                                                                          
           </button>
          {openSources && (
            <div className="source-list" style={{ marginTop: "5px", display: "flex", flexDirection: "column", gap: "5px", paddingLeft: "15px" }}>
              {sources.map((src, i) => (
                <div className="source-card" key={i} style={{ display: "flex", gap: "8px", alignItems: "flex-start", backgroundColor: "#111827", padding: "8px", borderRadius: "6px" }}>
                  <div className="source-icon">📄</div>
                  <div className="source-info" style={{ flex: 1 }}>
                    <div className="si-title" style={{ fontWeight: "bold", color: "#f9fafb" }}>{src.chunk_title || "Untitled section"}</div>                                                                                                           
                    <div className="si-doc" style={{ fontSize: "11px", color: "#9ca3af" }}>{src.document_name || "Unknown document"}</div>                                                                                                         
                  </div>
                  <span className="sim-badge" style={{ backgroundColor: "#374151", color: "#f3f4f6", padding: "2px 6px", borderRadius: "10px", fontSize: "10px" }}>
                    {Math.round((src.similarity || 0) * 100)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="msg-actions" style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
        <button style={{ background: isSpeaking ? "#4b5563" : "#1f2937", color: "#f3f4f6", border: "1px solid #374151", padding: "4px 8px", borderRadius: "4px", cursor: "pointer", transition: "background 0.2s" }} onMouseOver={e=>e.currentTarget.style.backgroundColor="#374151"} onMouseOut={e=>!isSpeaking && (e.currentTarget.style.backgroundColor="#1f2937")} onClick={onSpeak}>
          {isSpeaking ? "⏹ Stop" : "🔊 Listen"}
        </button>
        <button style={{ background: "#1f2937", color: "#f3f4f6", border: "1px solid #374151", padding: "4px 8px", borderRadius: "4px", cursor: "pointer", transition: "background 0.2s" }} onMouseOver={e=>e.currentTarget.style.backgroundColor="#374151"} onMouseOut={e=>e.currentTarget.style.backgroundColor="#1f2937"} onClick={onReplay}>🔁 Replay</button>
        <button
          style={{ background: "#1f2937", color: "#f3f4f6", border: "1px solid #374151", padding: "4px 8px", borderRadius: "4px", cursor: "pointer", transition: "background 0.2s" }}
          onMouseOver={e=>e.currentTarget.style.backgroundColor="#374151"} onMouseOut={e=>e.currentTarget.style.backgroundColor="#1f2937"} 
          onClick={() => { navigator.clipboard.writeText(message.text); }}
        >
          📋 Copy
        </button>
      </div>
    </div>
  )
}

/* ---------- ChatGPT-style word-by-word reveal with cursor ---------- */
function TypewriterText({ text, active, onComplete }) {
  const [shown, setShown] = useState(active ? "" : text)
  const [done, setDone] = useState(!active)
  const cbRef = useRef(onComplete)
  cbRef.current = onComplete

  useEffect(() => {
    if (!active) { setShown(text); setDone(true); return }
    setShown("")
    setDone(false)
    const words = text.split(" ")
    let i = 0
    const id = setInterval(() => {
      if (i < words.length) {
        setShown(words.slice(0, i + 1).join(" "))
        i++
      } else {
        clearInterval(id)
        setDone(true)
        cbRef.current && cbRef.current()
      }
    }, 38)
    return () => clearInterval(id)
  }, [text, active])

  return (
    <span>
      {shown}
      {!done && <span style={{ display: "inline-block", width: "6px", height: "16px", backgroundColor: "#f9fafb", verticalAlign: "middle", marginLeft: "2px", animation: "blink 1s step-end infinite" }} />}
    </span>
  )
}

export default Chatbot
