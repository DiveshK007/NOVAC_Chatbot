import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import Login from "./pages/Login"
import Chatbot from "./pages/Chatbot"
import Chunks from "./pages/Chunks"

function ProtectedRoute({ children }) {
  const token = localStorage.getItem("token")
  if (!token) {
    return <Navigate to="/" />
  }
  return children
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Login />} />
        <Route
          path="/chat"
          element={
            <ProtectedRoute>
              <Chatbot />
            </ProtectedRoute>
          }
        />
        <Route
          path="/chunks"
          element={
            <ProtectedRoute>
              <Chunks />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App