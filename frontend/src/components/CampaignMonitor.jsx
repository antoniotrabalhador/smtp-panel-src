import { useEffect, useRef, useState } from "react"

const REFRESH_INTERVAL = 5 // seconds

function ProgressBar({ value, total, color = "#238636", height = 8, animated = false }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0
  return (
    <div style={{ background: "#21262d", borderRadius: 4, height, overflow: "hidden", flex: 1, minWidth: 40 }}>
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: color,
          borderRadius: 4,
          transition: animated ? "width 1s ease" : "width 0.4s ease",
        }}
      />
    </div>
  )
}

function StatusBadge({ status }) {
  const config = {
    running:   { label: "em execução", color: "#d29922", bg: "#2d2007" },
    paused:    { label: "pausada",     color: "#8b949e", bg: "#1c2128" },
    done:      { label: "concluída",   color: "#3fb950", bg: "#0f2d18" },
    pending:   { label: "aguardando",  color: "#58a6ff", bg: "#0d2238" },
    scheduled: { label: "agendada",   color: "#a371f7", bg: "#1e1233" },
  }
  const c = config[status] || { label: status, color: "#8b949e", bg: "#1c2128" }
  return (
    <span style={{ fontSize: "0.72em", padding: "2px 8px", borderRadius: 10, background: c.bg, color: c.color, fontWeight: 600 }}>
      {c.label}
    </span>
  )
}

function formatNumber(n) {
  if (n == null) return "—"
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k"
  return String(n)
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return "—"
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}min`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m > 0 ? `${h}h ${m}min` : `${h}h`
}

function computeStats(campaign, prevSnapshot) {
  const now = Date.now()

  // Real rate: delta sent / delta time using previous snapshot
  let realRate = 0
  if (prevSnapshot && prevSnapshot.sent < campaign.sent) {
    const dt = (now - prevSnapshot.ts) / 1000 / 3600 // hours
    if (dt > 0) realRate = Math.round((campaign.sent - prevSnapshot.sent) / dt)
  }

  // ETA: remaining / rate
  const remaining = campaign.total - campaign.sent
  const rate = realRate || campaign.rate_per_hour || 0
  const etaSecs = rate > 0 ? (remaining / rate) * 3600 : null

  // Elapsed since started_at
  let elapsedSecs = null
  if (campaign.started_at) {
    elapsedSecs = (now - new Date(campaign.started_at).getTime()) / 1000
  }

  return { realRate, etaSecs, elapsedSecs }
}

function CampaignCard({ campaign, prevSnapshot, onPause, onResume, onSyncPostfix, syncing }) {
  const { realRate, etaSecs, elapsedSecs } = computeStats(campaign, prevSnapshot)
  const isRunning = campaign.status === "running"
  const isPaused = campaign.status === "paused"

  const barColor = isPaused ? "#6e7681" : campaign.errors > 0 ? "#f0883e" : "#238636"

  return (
    <div style={{ padding: 20, borderRadius: 12, border: `1px solid ${isRunning ? "#1f6feb" : "#30363d"}`, background: "#0d1117", display: "grid", gap: 16 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <strong style={{ fontSize: "1.05em" }}>{campaign.name}</strong>
              <StatusBadge status={campaign.status} />
              {isRunning && (
                <span style={{ fontSize: "0.7em", color: "#3fb950", animation: "pulse 2s infinite" }}>● AO VIVO</span>
              )}
              {campaign.status === "scheduled" && campaign.scheduled_at && (() => {
                const diff = new Date(campaign.scheduled_at) - new Date()
                if (diff <= 0) return <span style={{ fontSize: "0.7em", color: "#a371f7" }}>⌛ Iniciando...</span>
                const h = Math.floor(diff / 3600000)
                const m = Math.floor((diff % 3600000) / 60000)
                const s = Math.floor((diff % 60000) / 1000)
                const label = h > 0 ? `${h}h ${m}min` : m > 0 ? `${m}min ${s}s` : `${s}s`
                return <span style={{ fontSize: "0.7em", color: "#a371f7" }}>⏰ Dispara em {label}</span>
              })()}
            </div>
            <div style={{ fontSize: "0.8em", color: "#8b949e" }}>{campaign.subject || "Sem assunto"}</div>
          </div>
        <div style={{ display: "flex", gap: 6 }}>
          {isRunning && (
            <button
              onClick={() => onPause(campaign.id)}
              style={{ fontSize: "0.78em", padding: "4px 12px", background: "#3d2424", color: "#f0883e", border: "1px solid #f0883e", borderRadius: 6, cursor: "pointer" }}
            >
              ⏸ Pausar
            </button>
          )}
          {isPaused && (
            <button
              onClick={() => onResume(campaign.id)}
              style={{ fontSize: "0.78em", padding: "4px 12px", background: "#0d2e0d", color: "#3fb950", border: "1px solid #3fb950", borderRadius: 6, cursor: "pointer" }}
            >
              ▶ Retomar
            </button>
          )}
          <button
            onClick={() => onSyncPostfix(campaign.id)}
            disabled={syncing}
            title="Puxa contagem real do /var/log/mail.log de cada VPS via SSH"
            style={{ fontSize: "0.78em", padding: "4px 12px", background: syncing ? "#21262d" : "#0d2238", color: syncing ? "#6e7681" : "#58a6ff", border: "1px solid #1f6feb", borderRadius: 6, cursor: syncing ? "not-allowed" : "pointer" }}
          >
            {syncing ? "⟳ Sincronizando..." : "↻ Sync Postfix"}
          </button>
          <a
            href={`/api/campaigns/${campaign.id}/download-logs`}
            download
            title="Baixa os logs do Postfix de todas as VPS num arquivo .tar.gz"
            style={{ fontSize: "0.78em", padding: "4px 12px", background: "#238636", color: "#ffffff", border: "1px solid #2ea043", borderRadius: 6, cursor: "pointer", textDecoration: "none", display: "flex", alignItems: "center" }}
          >
            📥 Baixar Logs
          </a>
        </div>
      </div>

      {/* Main progress */}
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <ProgressBar value={campaign.sent} total={campaign.total} color={barColor} height={14} animated={isRunning} />
          <span style={{ fontSize: "1em", fontWeight: 700, color: "#c9d1d9", minWidth: 44 }}>{campaign.pct}%</span>
        </div>
        <div style={{ display: "flex", gap: 24, fontSize: "0.82em", color: "#8b949e" }}>
          <span>📤 <strong style={{ color: "#c9d1d9" }}>{formatNumber(campaign.sent)}</strong> enviados</span>
          <span>🎯 <strong style={{ color: "#c9d1d9" }}>{formatNumber(campaign.total)}</strong> total</span>
          {campaign.errors > 0 && (
            <span>⚠ <strong style={{ color: "#f85149" }}>{formatNumber(campaign.errors)}</strong> erros</span>
          )}
          <span>📦 chunk {formatNumber(campaign.chunk_size)}</span>
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
        <StatBox label="Taxa alvo" value={campaign.rate_per_hour ? `${formatNumber(campaign.rate_per_hour)}/h` : "sem limite"} color="#8b949e" />
        <StatBox label="Taxa real" value={realRate > 0 ? `${formatNumber(realRate)}/h` : "—"} color={realRate > 0 ? "#58a6ff" : "#8b949e"} />
        <StatBox label="Restantes" value={formatNumber(campaign.total - campaign.sent)} color="#c9d1d9" />
        <StatBox label="ETA" value={formatDuration(etaSecs)} color={etaSecs && etaSecs < 3600 ? "#3fb950" : "#c9d1d9"} />
        <StatBox label="Decorrido" value={formatDuration(elapsedSecs)} color="#8b949e" />
        <StatBox label="VPS ativas" value={`${campaign.shards.filter(s => s.status === "running" || s.status === "pending").length}/${campaign.shards.length}`} color="#d29922" />
      </div>

      {/* Per-shard breakdown */}
      {campaign.shards.length > 0 && (
        <div>
          <div style={{ fontSize: "0.78em", color: "#8b949e", fontWeight: 600, marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Progresso por VPS
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {campaign.shards.map(shard => (
              <div key={shard.shard_id} style={{ display: "grid", gridTemplateColumns: "130px 1fr 44px 50px 80px", alignItems: "center", gap: 8, padding: "8px 10px", borderRadius: 8, background: "#161b22", border: "1px solid #21262d" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: shardDotColor(shard.status), flexShrink: 0, display: "inline-block" }} />
                  <span style={{ fontSize: "0.8em", color: "#c9d1d9", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{shard.node_name}</span>
                </div>
                <ProgressBar
                  value={shard.sent}
                  total={shard.total}
                  color={shardBarColor(shard.status)}
                  height={6}
                  animated={shard.status === "running"}
                />
                <span style={{ fontSize: "0.75em", color: "#8b949e", textAlign: "right" }}>{shard.pct}%</span>
                <span style={{ fontSize: "0.72em", color: "#8b949e", textAlign: "right" }}>{formatNumber(shard.sent)}</span>
                <div style={{ textAlign: "right" }}>
                  <span style={{ fontSize: "0.68em", color: "#6e7681" }}>chunk {shard.current_chunk}/{shard.total_chunks}</span>
                  {shard.errors > 0 && <div style={{ fontSize: "0.68em", color: "#f85149" }}>⚠ {shard.errors} err</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatBox({ label, value, color }) {
  return (
    <div style={{ padding: "10px 12px", borderRadius: 8, background: "#161b22", border: "1px solid #21262d" }}>
      <div style={{ fontSize: "0.7em", color: "#6e7681", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: "1em", fontWeight: 700, color }}>{value}</div>
    </div>
  )
}

function shardDotColor(status) {
  return { running: "#3fb950", pending: "#58a6ff", paused: "#6e7681", done: "#30363d", failed: "#f85149" }[status] || "#6e7681"
}

function shardBarColor(status) {
  return { running: "#1f6feb", pending: "#388bfd", paused: "#6e7681", done: "#3fb950", failed: "#f85149" }[status] || "#6e7681"
}

export default function CampaignMonitor() {
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [syncing, setSyncing] = useState({})
  const prevSnapshotsRef = useRef({}) // campaignId -> { sent, ts }

  async function fetchActive() {
    try {
      const res = await fetch("/api/campaigns/active")
      if (res.ok) {
        const data = await res.json()
        // Store snapshots for rate calculation
        const now = Date.now()
        setCampaigns(prev => {
          prev.forEach(c => {
            prevSnapshotsRef.current[c.id] = { sent: c.sent, ts: now }
          })
          return data
        })
        setLastUpdated(new Date())
      }
    } catch (_) {
      // network error — keep showing last data
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchActive()
    const interval = setInterval(fetchActive, REFRESH_INTERVAL * 1000)
    return () => clearInterval(interval)
  }, [])

  // Countdown ticker
  useEffect(() => {
    setCountdown(REFRESH_INTERVAL)
    const t = setInterval(() => {
      setCountdown(c => (c <= 1 ? REFRESH_INTERVAL : c - 1))
    }, 1000)
    return () => clearInterval(t)
  }, [lastUpdated])

  async function handlePause(id) {
    await fetch(`/api/campaigns/${id}/pause`, { method: "POST" })
    fetchActive()
  }

  async function handleResume(id) {
    await fetch(`/api/campaigns/${id}/resume`, { method: "POST" })
    fetchActive()
  }

  async function handleSyncPostfix(id) {
    setSyncing(prev => ({ ...prev, [id]: true }))
    try {
      const res = await fetch(`/api/campaigns/${id}/sync-postfix-stats`, { method: "POST" })
      const data = await res.json()
      if (data.ok) {
        await fetchActive()
      }
    } finally {
      setSyncing(prev => ({ ...prev, [id]: false }))
    }
  }

  return (
    <div style={{ display: "grid", gap: 18 }}>
      {/* Header bar */}
      <div style={{ display: "flex", alignItems: "center", padding: "12px 16px", borderRadius: 10, background: "#161b22", border: "1px solid #30363d" }}>
        <div>
          <span style={{ fontWeight: 700, fontSize: "1.05em" }}>📡 Monitor de Campanhas</span>
          <span style={{ marginLeft: 12, fontSize: "0.8em", color: "#8b949e" }}>
            {campaigns.length > 0
              ? [
                  campaigns.filter(c => c.status === "running").length > 0 && `${campaigns.filter(c => c.status === "running").length} em execução`,
                  campaigns.filter(c => c.status === "paused").length > 0 && `${campaigns.filter(c => c.status === "paused").length} pausadas`,
                  campaigns.filter(c => c.status === "scheduled").length > 0 && `${campaigns.filter(c => c.status === "scheduled").length} agendadas`,
                ].filter(Boolean).join(" · ") || "Nenhuma campanha ativa"
              : "Nenhuma campanha ativa"}
          </span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
          {lastUpdated && (
            <span style={{ fontSize: "0.75em", color: "#6e7681" }}>
              Atualizado: {lastUpdated.toLocaleTimeString("pt-BR")}
            </span>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 28, height: 28, borderRadius: "50%", background: "#21262d", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.75em", fontWeight: 700, color: countdown <= 2 ? "#3fb950" : "#8b949e" }}>
              {countdown}
            </div>
            <button
              onClick={() => { fetchActive(); setCountdown(REFRESH_INTERVAL) }}
              style={{ fontSize: "0.8em", padding: "4px 10px", borderRadius: 6, background: "transparent", color: "#58a6ff", border: "1px solid #1f6feb", cursor: "pointer" }}
            >
              ↻ Atualizar
            </button>
          </div>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: "center", padding: 40, color: "#8b949e" }}>Carregando campanhas ativas...</div>
      )}

      {/* Empty state */}
      {!loading && campaigns.length === 0 && (
        <div style={{ textAlign: "center", padding: 60, borderRadius: 12, border: "1px dashed #30363d", background: "#0d1117" }}>
          <div style={{ fontSize: "2.5em", marginBottom: 12 }}>📭</div>
          <div style={{ color: "#8b949e", fontSize: "0.95em" }}>Nenhuma campanha em execução no momento.</div>
          <div style={{ color: "#6e7681", fontSize: "0.82em", marginTop: 6 }}>
            Lançe uma campanha na aba 🚀 Campanhas para ver o progresso aqui.
          </div>
        </div>
      )}

      {/* Campaign cards */}
      {campaigns.map(campaign => (
        <CampaignCard
          key={campaign.id}
          campaign={campaign}
          prevSnapshot={prevSnapshotsRef.current[campaign.id]}
          onPause={handlePause}
          onResume={handleResume}
          onSyncPostfix={handleSyncPostfix}
          syncing={!!syncing[campaign.id]}
        />
      ))}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  )
}
