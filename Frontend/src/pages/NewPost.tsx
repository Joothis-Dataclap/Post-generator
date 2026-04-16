import { useMemo, useState } from 'react'
import { postJson } from '../lib/api'
import { INDUSTRIES } from '../lib/industries'

type ContentIdea = {
  id: string
  title: string
  angle: string
  core_hook: string
  knowledge_source?: string
  trend_source?: string
  target_audience?: string
  engagement_potential?: string
}

type IdeaResponse = {
  bundle_id: string
  industry: string
  context_summary: string
  ideas: ContentIdea[]
  research_insights?: string
}

type ContentGenerateResponse = {
  bundle_id: string
  idea_id: string
  idea_title: string
  linkedin_type: string
  x_type: string
  linkedin_content: Record<string, unknown> | null
  x_content: Record<string, unknown> | null
  content_notes?: string
  draft_id: string
}

type DraftApproveResponse = {
  id: string
  status: string
  scheduled_at: string | null
  linkedin_post_id: string | null
  x_post_id: string | null
}

const fallbackIdeas = [
  {
    id: 'fallback-1',
    title: 'The quiet bottleneck hiding in AI operations',
    angle: 'myth-busting',
    core_hook:
      'Everyone talks about model accuracy, but operations overhead is quietly eating 40% of AI budgets.',
    target_audience: 'AI ops leads, CTOs, product owners',
  },
  {
    id: 'fallback-2',
    title: 'What most teams miss in their first production rollout',
    angle: 'playbook',
    core_hook:
      'Your first production launch needs a measurement plan before a model plan. Otherwise adoption stalls.',
    target_audience: 'Product and engineering leaders',
  },
  {
    id: 'fallback-3',
    title: 'How to defend ROI when AI costs rise',
    angle: 'data-story',
    core_hook:
      'Moving from pilot to scale doubles cost without doubling value unless you redesign workflows.',
    target_audience: 'Finance, strategy, and delivery teams',
  },
]

export default function NewPost() {
  const [industry, setIndustry] = useState(INDUSTRIES[0])
  const [serviceDescription, setServiceDescription] = useState('')
  const [ideas, setIdeas] = useState<ContentIdea[] | null>(null)
  const [bundleId, setBundleId] = useState<string | null>(null)
  const [contextSummary, setContextSummary] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedIdea, setSelectedIdea] = useState<ContentIdea | null>(null)
  const [linkedinType, setLinkedinType] = useState('single')
  const [xType, setXType] = useState('thread')
  const [brandVoice, setBrandVoice] = useState('authoritative but direct')
  const [targetAudience, setTargetAudience] = useState('')
  const [draftStatus, setDraftStatus] = useState<string | null>(null)
  const [draftResponse, setDraftResponse] = useState<
    ContentGenerateResponse | null
  >(null)
  const [approveStatus, setApproveStatus] = useState<string | null>(null)
  const [scheduledAt, setScheduledAt] = useState('')
  const [generatingContent, setGeneratingContent] = useState(false)
  const [approving, setApproving] = useState(false)

  const filteredIdeas = useMemo(() => {
    if (ideas && ideas.length) return ideas
    return null
  }, [ideas])

  const handleGenerate = async (event: React.FormEvent) => {
    event.preventDefault()
    setStatus(null)
    setLoading(true)
    setDraftResponse(null)
    setDraftStatus(null)
    setApproveStatus(null)

    const result = await postJson<IdeaResponse>('/ideas/generate', {
      industry,
      service_description: serviceDescription || undefined,
    })

    setLoading(false)

    if (!result.ok || !result.data) {
      setStatus(result.error ?? 'Idea generation failed. Showing sample ideas.')
      setIdeas([])
      return
    }

    setBundleId(result.data.bundle_id)
    setContextSummary(result.data.context_summary)
    setIdeas(result.data.ideas)
    setSelectedIdea(null)
  }

  const handleCreateContent = async () => {
    if (!bundleId || !selectedIdea) {
      setDraftStatus('Select an idea first.')
      return
    }

    setDraftStatus(null)
    setGeneratingContent(true)

    const result = await postJson<ContentGenerateResponse>('/content/generate', {
      bundle_id: bundleId,
      idea_id: selectedIdea.id,
      linkedin_type: linkedinType,
      x_type: xType,
      brand_voice: brandVoice,
      target_audience: targetAudience || undefined,
    })

    setGeneratingContent(false)

    if (!result.ok || !result.data) {
      setDraftStatus(result.error ?? 'Failed to generate content.')
      return
    }

    setDraftResponse(result.data)
    setDraftStatus(`Draft created: ${result.data.draft_id}`)
  }

  const handleApprove = async () => {
    if (!draftResponse?.draft_id) {
      setApproveStatus('Generate a draft before approving.')
      return
    }

    setApproveStatus(null)
    setApproving(true)

    const result = await postJson<DraftApproveResponse>(
      `/drafts/${draftResponse.draft_id}/approve`,
      {
        publish_linkedin: true,
        publish_x: true,
        scheduled_at: scheduledAt || null,
        linkedin_content_override: null,
        x_content_override: null,
      },
    )

    setApproving(false)

    if (!result.ok || !result.data) {
      setApproveStatus(result.error ?? 'Approval failed.')
      return
    }

    setApproveStatus(`Draft status: ${result.data.status}`)
  }

  return (
    <div className="stack">
      <section className="hero-card">
        <div>
          <p className="eyebrow">New Post</p>
          <h2>Create a post from an industry idea</h2>
          <p className="muted">
            Start with an industry, add a focus area, and let the idea engine
            assemble a shortlist.
          </p>
        </div>
        <div className="hero-actions">
          <a className="primary-button" href="#idea-gen">
            Create Post
          </a>
          <button className="ghost-button" type="button">
            Save draft
          </button>
        </div>
      </section>

      <section id="idea-gen" className="card">
        <div className="card-header">
          <h3>Idea generation</h3>
          {bundleId ? <span className="badge">Bundle {bundleId}</span> : null}
        </div>
        <form className="form-grid" onSubmit={handleGenerate}>
          <label className="field">
            <span>Industry</span>
            <select
              value={industry}
              onChange={(event) => setIndustry(event.target.value)}
            >
              {INDUSTRIES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Service description</span>
            <input
              value={serviceDescription}
              onChange={(event) => setServiceDescription(event.target.value)}
              placeholder="Example: AI data annotation for NLP and safety review"
            />
          </label>
          <button className="primary-button" type="submit" disabled={loading}>
            {loading ? 'Generating...' : 'Generate Ideas'}
          </button>
          {status ? <p className="status">{status}</p> : null}
        </form>
        {contextSummary ? (
          <div className="context-summary">
            <p className="info-label">Context summary</p>
            <p>{contextSummary}</p>
          </div>
        ) : null}
      </section>

      <section className="grid idea-grid">
        {(filteredIdeas && filteredIdeas.length ? filteredIdeas : fallbackIdeas).map(
          (idea, index) => (
            <article key={idea.title} className="card idea-card">
              <div className="idea-header">
                <span className="badge">Idea {index + 1}</span>
                <span className="muted">{idea.angle}</span>
              </div>
              <h3>{idea.title}</h3>
              <p className="idea-hook">{idea.core_hook}</p>
              <p className="muted">
                Audience: {idea.target_audience ?? 'Decision makers'}
              </p>
              <button
                className={
                  selectedIdea?.title === idea.title
                    ? 'primary-button'
                    : 'ghost-button'
                }
                type="button"
                onClick={() => setSelectedIdea(idea)}
              >
                {selectedIdea?.title === idea.title
                  ? 'Selected'
                  : 'Use this idea'}
              </button>
            </article>
          ),
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <h3>Content generation</h3>
          <span className="badge">Step 2</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>LinkedIn type</span>
            <select
              value={linkedinType}
              onChange={(event) => setLinkedinType(event.target.value)}
            >
              <option value="single">Single</option>
              <option value="carousel">Carousel</option>
              <option value="article">Article</option>
            </select>
          </label>
          <label className="field">
            <span>X type</span>
            <select value={xType} onChange={(event) => setXType(event.target.value)}>
              <option value="tweet">Tweet</option>
              <option value="thread">Thread</option>
              <option value="carousel">Carousel</option>
            </select>
          </label>
          <label className="field">
            <span>Brand voice</span>
            <input
              value={brandVoice}
              onChange={(event) => setBrandVoice(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Target audience</span>
            <input
              value={targetAudience}
              onChange={(event) => setTargetAudience(event.target.value)}
              placeholder="Fintech founders, CTOs"
            />
          </label>
          <button
            className="primary-button"
            type="button"
            onClick={handleCreateContent}
            disabled={generatingContent}
          >
            {generatingContent ? 'Creating...' : 'Create Post'}
          </button>
          {draftStatus ? <p className="status">{draftStatus}</p> : null}
        </div>
        {draftResponse ? (
          <div className="content-preview">
            <div>
              <p className="info-label">LinkedIn content</p>
              <pre>{JSON.stringify(draftResponse.linkedin_content, null, 2)}</pre>
            </div>
            <div>
              <p className="info-label">X content</p>
              <pre>{JSON.stringify(draftResponse.x_content, null, 2)}</pre>
            </div>
          </div>
        ) : null}
      </section>

      <section className="card">
        <div className="card-header">
          <h3>Approve & publish</h3>
          <span className="badge">Step 3</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>Schedule (optional ISO)</span>
            <input
              value={scheduledAt}
              onChange={(event) => setScheduledAt(event.target.value)}
              placeholder="2026-04-17T09:00:00Z"
            />
          </label>
          <button
            className="primary-button"
            type="button"
            onClick={handleApprove}
            disabled={approving}
          >
            {approving ? 'Approving...' : 'Approve & publish'}
          </button>
          {approveStatus ? <p className="status">{approveStatus}</p> : null}
        </div>
      </section>
    </div>
  )
}
