const stats = [
  { label: 'Posts Published', value: '128', trend: '+12 this week' },
  { label: 'Ideas Generated', value: '412', trend: '+38 in 7 days' },
  { label: 'Drafts in Review', value: '9', trend: '3 waiting approval' },
  { label: 'Avg. Engagement', value: '7.4%', trend: 'Up from 6.1%' },
]

const activity = [
  {
    title: 'Fintech insights carousel scheduled',
    meta: 'LinkedIn · Apr 16 · 09:00 AM',
  },
  {
    title: 'Manufacturing AI thread approved',
    meta: 'X · Apr 15 · 04:30 PM',
  },
  {
    title: 'Insurance idea bundle generated',
    meta: 'Research · Apr 15 · 11:02 AM',
  },
  {
    title: 'Retail sentiment report ingested',
    meta: 'Sources · Apr 14 · 08:40 PM',
  },
]

export default function Dashboard() {
  return (
    <div className="stack">
      <section className="hero-card">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h2>Momentum at a glance</h2>
          <p className="muted">
            A quick look at what is posted, pending, and heating up across your
            content pipeline.
          </p>
        </div>
        <div className="hero-actions">
          <button className="primary-button" type="button">
            Review Drafts
          </button>
          <button className="ghost-button" type="button">
            View Analytics
          </button>
        </div>
      </section>

      <section className="grid stats-grid">
        {stats.map((item) => (
          <article key={item.label} className="card stat-card">
            <p className="stat-label">{item.label}</p>
            <p className="stat-value">{item.value}</p>
            <p className="stat-trend">{item.trend}</p>
          </article>
        ))}
      </section>

      <section className="grid insights-grid">
        <article className="card">
          <h3>Top performing themes</h3>
          <ul className="pill-list">
            <li>Regulatory shifts</li>
            <li>Applied AI wins</li>
            <li>Automation ROI</li>
            <li>Buyer enablement</li>
          </ul>
          <p className="muted">
            Posts tied to industry benchmarks are seeing the strongest saves and
            re-shares.
          </p>
        </article>
        <article className="card">
          <h3>Upcoming schedule</h3>
          <div className="schedule">
            <div>
              <p className="schedule-time">Apr 17 · 09:00 AM</p>
              <p className="schedule-title">Medical AI: compliance readiness</p>
            </div>
            <div>
              <p className="schedule-time">Apr 18 · 02:30 PM</p>
              <p className="schedule-title">Retail personalization blueprint</p>
            </div>
            <div>
              <p className="schedule-time">Apr 19 · 11:00 AM</p>
              <p className="schedule-title">Sports & Media fan growth stack</p>
            </div>
          </div>
        </article>
      </section>

      <section className="card">
        <div className="card-header">
          <h3>Recent activity</h3>
          <button className="text-button" type="button">
            View all
          </button>
        </div>
        <div className="activity-list">
          {activity.map((item) => (
            <div key={item.title} className="activity-item">
              <div>
                <p className="activity-title">{item.title}</p>
                <p className="activity-meta">{item.meta}</p>
              </div>
              <span className="badge">Live</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
