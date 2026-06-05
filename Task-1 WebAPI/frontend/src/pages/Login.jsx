import { useNavigate } from "react-router-dom"
import { useState, useEffect } from "react"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)

  const [captchaAnswer, setCaptchaAnswer] = useState("")
  const [captchaInput, setCaptchaInput] = useState("")
  const [captchaQuestion, setCaptchaQuestion] = useState("")

  const generateCaptcha = () => {
    const num1 = Math.floor(Math.random() * 10) + 1
    const num2 = Math.floor(Math.random() * 10) + 1
    setCaptchaQuestion(`${num1} + ${num2}`)
    setCaptchaAnswer((num1 + num2).toString())
    setCaptchaInput("")
  }

  useEffect(() => {
    generateCaptcha()
  }, [])

  const handleLogin = async () => {
    if (captchaInput !== captchaAnswer) {
      alert("Incorrect CAPTCHA answer.")
      generateCaptcha()
      return
    }

    setLoading(true)
    try {
      const response = await fetch(`${API}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      })
      const data = await response.json()
      if (data.success) {
        localStorage.setItem("isAuthenticated", "true")
        localStorage.setItem("token", data.token)
        localStorage.setItem("userEmail", data.user.email)
        localStorage.setItem("userRole", data.user.role)
        navigate("/chat")
      } else {
        alert(data.message)
      }
    } catch {
      alert("Backend connection failed")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-wrap" style={{ backgroundColor: "#111827", minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div className="auth-card" style={{ backgroundColor: "#1f2937", border: "1px solid #374151" }}>
        <div className="auth-logo">
          <div className="brand-mark" style={{ backgroundColor: "#3b82f6" }}>N</div>
          <div style={{ textAlign: "center" }}>
            <h1 className="auth-title" style={{ color: "#f9fafb" }}>NOVAC AI</h1>
            <p className="auth-sub" style={{ color: "#9ca3af" }}>Multilingual Enterprise RAG Assistant</p>
          </div>
        </div>

        <div className="auth-field-group">
          <input
            className="field"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            style={{ backgroundColor: "#111827", color: "white", border: "1px solid #374151" }}
          />
          <div className="pw-wrap" style={{ position: "relative" }}>
            <input
              className="field"
              type={showPassword ? "text" : "password"}
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              style={{ backgroundColor: "#111827", color: "white", border: "1px solid #374151", width: "100%", paddingRight: "40px" }}
            />
            <button className="pw-toggle" onClick={() => setShowPassword(!showPassword)} style={{ color: "#9ca3af" }}>
              {showPassword ? "🙈" : "👁"}
            </button>
          </div>
          
          <div style={{ marginTop: "15px", display: "flex", gap: "10px", alignItems: "center" }}>
            <div style={{ backgroundColor: "#374151", color: "#f9fafb", padding: "10px", borderRadius: "6px", fontWeight: "bold" }}>
              {captchaQuestion} = ?
            </div>
            <input
              className="field"
              type="text"
              placeholder="CAPTCHA"
              value={captchaInput}
              onChange={(e) => setCaptchaInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              style={{ flex: 1, marginTop: 0, backgroundColor: "#111827", color: "white", border: "1px solid #374151" }}
            />
            <button onClick={generateCaptcha} style={{ background: "none", border: "none", cursor: "pointer", fontSize: "20px" }}>
              🔄
            </button>
          </div>
        </div>

        <button className="btn-primary auth-btn" onClick={handleLogin} disabled={loading || !captchaInput} style={{ backgroundColor: "#3b82f6" }}>
          {loading ? "Signing in…" : "Sign In"}
        </button>

        <div className="auth-hint" style={{ color: "#6b7280" }}>Use your NOVAC credentials to continue</div>
      </div>
    </div>
  )
}

export default Login
