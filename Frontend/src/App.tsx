import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import NewPost from './pages/NewPost'

function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">DC</span>
          <div>
            <p className="brand-title">Dataclap</p>
            <p className="brand-subtitle">Content Control</p>
          </div>
        </div>
        <nav className="nav">
          <NavLink to="/" className="nav-link" end>
            Dashboard
          </NavLink>
          <NavLink to="/new-post" className="nav-link">
            New Post
          </NavLink>
          <NavLink to="/settings" className="nav-link">
            Settings
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <p className="muted">API: http://localhost:8000/api/v1</p>
          <p className="muted">Local build</p>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">Post Generator</p>
            <h1>Idea-to-Post Studio</h1>
          </div>
          <div className="topbar-actions">
            <button className="ghost-button" type="button">
              Sync Sources
            </button>
            <button className="primary-button" type="button">
              Create Post
            </button>
          </div>
        </header>

        <section className="page-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/new-post" element={<NewPost />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </section>
      </main>
    </div>
  )
}

export default App
