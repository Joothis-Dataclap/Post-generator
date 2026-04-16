import { useMemo, useState } from 'react'
import { getJson, postForm } from '../lib/api'

type SourceResponse = {
  id: string
  title: string
  source_type: string
  category: string
  filename: string | null
  chunk_count: number
  created_at: string
}

type SourceWithChunks = SourceResponse & {
  raw_text?: string
  chunks?: Array<Record<string, unknown>>
}

export default function Settings() {
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('general')
  const [sourceType, setSourceType] = useState('report')
  const [file, setFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [source, setSource] = useState<SourceResponse | null>(null)
  const [chunkData, setChunkData] = useState<SourceWithChunks | null>(null)
  const [loading, setLoading] = useState(false)

  const fileLabel = useMemo(() => {
    if (!file) return 'No file selected'
    return `${file.name} · ${(file.size / 1024).toFixed(1)} KB`
  }, [file])

  const handleUpload = async (event: React.FormEvent) => {
    event.preventDefault()
    setStatus(null)

    if (!file || !title.trim()) {
      setStatus('Add a title and choose a PDF to upload.')
      return
    }

    const formData = new FormData()
    formData.append('title', title)
    formData.append('source_type', sourceType)
    formData.append('category', category)
    formData.append('file', file)

    setLoading(true)
    const result = await postForm<SourceResponse>('/sources', formData)
    setLoading(false)

    if (!result.ok || !result.data) {
      setStatus(result.error ?? 'Upload failed. Please try again.')
      return
    }

    setSource(result.data)
    setStatus('Upload complete. Fetching chunk data...')

    const chunksResult = await getJson<SourceWithChunks>(
      `/sources/${result.data.id}`,
    )

    if (!chunksResult.ok || !chunksResult.data) {
      setStatus(chunksResult.error ?? 'Could not load chunk data.')
      return
    }

    setChunkData(chunksResult.data)
    setStatus('Chunk data loaded.')
  }

  return (
    <div className="stack">
      <section className="hero-card">
        <div>
          <p className="eyebrow">Settings</p>
          <h2>Knowledge base intake</h2>
          <p className="muted">
            Upload a PDF and preview the chunks that will power idea generation.
          </p>
        </div>
        <div className="hero-actions">
          <button className="ghost-button" type="button">
            Configure API
          </button>
        </div>
      </section>

      <section className="card">
        <h3>PDF upload</h3>
        <form className="form-grid" onSubmit={handleUpload}>
          <label className="field">
            <span>Title</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="2026 AI benchmark report"
              required
            />
          </label>
          <label className="field">
            <span>Category</span>
            <input
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              placeholder="fintech"
            />
          </label>
          <label className="field">
            <span>Source type</span>
            <select
              value={sourceType}
              onChange={(event) => setSourceType(event.target.value)}
            >
              <option value="report">Report</option>
              <option value="article">Article</option>
              <option value="blog">Blog</option>
            </select>
          </label>
          <label className="field file-field">
            <span>PDF file</span>
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md,.html"
              onChange={(event) =>
                setFile(event.target.files ? event.target.files[0] : null)
              }
            />
            <p className="muted small">{fileLabel}</p>
          </label>
          <button className="primary-button" type="submit" disabled={loading}>
            {loading ? 'Uploading...' : 'Upload and chunk'}
          </button>
          {status ? <p className="status">{status}</p> : null}
        </form>
      </section>

      <section className="grid">
        <article className="card">
          <h3>Latest source</h3>
          {source ? (
            <div className="info-list">
              <div>
                <p className="info-label">Title</p>
                <p>{source.title}</p>
              </div>
              <div>
                <p className="info-label">Chunks</p>
                <p>{source.chunk_count}</p>
              </div>
              <div>
                <p className="info-label">Category</p>
                <p>{source.category}</p>
              </div>
              <div>
                <p className="info-label">Source type</p>
                <p>{source.source_type}</p>
              </div>
            </div>
          ) : (
            <p className="muted">Upload a PDF to see metadata here.</p>
          )}
        </article>
        <article className="card">
          <h3>Chunk preview</h3>
          {chunkData?.chunks?.length ? (
            <div className="chunk-list">
              {chunkData.chunks.slice(0, 6).map((chunk, index) => {
                const text =
                  (chunk.text as string) ||
                  (chunk.content as string) ||
                  JSON.stringify(chunk)
                return (
                  <div key={`${chunkData.id}-${index}`} className="chunk-item">
                    <p className="chunk-title">Chunk {index + 1}</p>
                    <p className="chunk-text">{text.slice(0, 240)}...</p>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="muted">
              Chunk data will appear after a successful upload.
            </p>
          )}
        </article>
      </section>
    </div>
  )
}
