import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { authHeader, handleUnauthorized } from "../auth"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

function Chunks() {
  const navigate = useNavigate()
  const [chunks, setChunks] = useState([])
  const [selectedDocs, setSelectedDocs] = useState([])

  useEffect(() => { fetchChunks() }, [])

  const fetchChunks = async () => {
    const response = await fetch(`${API}/chunks`, { headers: { ...authHeader() } })
    if (handleUnauthorized(response)) return
    const data = await response.json()
    setChunks(data.chunks)
  }

  const handleDeleteSelected = async () => {
    if (selectedDocs.length === 0) return
    if (!window.confirm(`Delete chunks for ${selectedDocs.length} document(s)?`)) return
    try {
      const response = await fetch(`${API}/chunks/documents`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: JSON.stringify({ document_names: selectedDocs }),
      })
      if (handleUnauthorized(response)) return
      const result = await response.json()
      alert(result.message)
      setSelectedDocs([])
      fetchChunks()
    } catch {
      alert("Failed to delete documents")
    }
  }

  const toggleDocSelection = (docName) => {
    setSelectedDocs((prev) =>
      prev.includes(docName) ? prev.filter((d) => d !== docName) : [...prev, docName]
    )
  }

  const updateChunk = async (chunk_id, updated_chunk) => {
    const response = await fetch(`${API}/update-chunk`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ chunk_id, updated_chunk }),
    })
    if (handleUnauthorized(response)) return
    alert("Chunk updated successfully")
  }

  const docNames = Array.from(new Set(chunks.map((c) => c.document_name || c.filename)))

  return (
    <div className="page-wrap">
      <div className="topbar" style={{ borderRadius: "var(--r-lg)", border: "1px solid var(--border)" }}>
        <button className="ghost-btn" onClick={() => navigate("/chat")}>← Back</button>
        <h1 style={{ marginLeft: 4 }}>Stored Chunks</h1>
        <div className="spacer" />
        <span className="pill">{chunks.length} chunks</span>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3 style={{ margin: 0 }}>Manage Documents</h3>
          <button
            className="ghost-btn danger"
            onClick={handleDeleteSelected}
            disabled={selectedDocs.length === 0}
            style={selectedDocs.length === 0 ? { opacity: 0.5, cursor: "not-allowed" } : {}}
          >
            🗑 Delete Selected{selectedDocs.length ? ` (${selectedDocs.length})` : ""}
          </button>
        </div>
        <div className="doc-chips">
          {docNames.map((docName) => (
            <label
              key={docName}
              className={`doc-chip ${selectedDocs.includes(docName) ? "checked" : ""}`}
            >
              <input
                type="checkbox"
                checked={selectedDocs.includes(docName)}
                onChange={() => toggleDocSelection(docName)}
              />
              {docName && docName.length > 30 ? docName.slice(0, 30) + "…" : docName || "Untitled"}
            </label>
          ))}
          {chunks.length === 0 && <span style={{ color: "var(--text-faint)" }}>No documents stored.</span>}
        </div>
      </div>

      {chunks.map((item) => (
        <div className="panel" key={item.chunk_id}>
          <div className="doc-tag">Document: {item.document_name || item.filename}</div>
          <h3 className="chunk-title">{item.chunk_title}</h3>
          <textarea
            className="chunk-textarea"
            rows="6"
            value={item.chunk}
            onChange={(e) =>
              setChunks((prev) =>
                prev.map((c) => (c.chunk_id === item.chunk_id ? { ...c, chunk: e.target.value } : c))
              )
            }
          />
          <button
            className="btn-primary"
            style={{ marginTop: 12 }}
            onClick={() => updateChunk(item.chunk_id, item.chunk)}
          >
            Save Changes
          </button>
        </div>
      ))}
    </div>
  )
}

export default Chunks
