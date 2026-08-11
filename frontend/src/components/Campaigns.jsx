import { useEffect, useMemo, useState } from "react"

function ProgressBar({ value, total, color = "#238636", height = 6 }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0
  return (
    <div style={{ background: "#21262d", borderRadius: 4, height, overflow: "hidden", flex: 1, minWidth: 60 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 0.5s ease", borderRadius: 4 }} />
    </div>
  )
}

function parseRecipients(text) {
  return Array.from(
    new Set(
      text
        .split(/\r?\n|,|;/)
        .map((item) => item.trim())
        .filter((item) => item && item.includes("@"))
    )
  )
}

function splitCounts(total, buckets) {
  if (!buckets) return []
  return Array.from({ length: buckets }, (_, index) => Math.floor((total + buckets - index - 1) / buckets))
}

function generatePreviewProtocol() {
  return "3847162905"
}

function buildHeaderPreview(node, template, toAddress, ctaUrl) {
  const protocol = generatePreviewProtocol()
  const recipient = toAddress || "destinatario@exemplo.com"
  const fromAddress = node.email_from || "remetente@exemplo.com"
  const domain = fromAddress.includes("@") ? fromAddress.split("@")[1] : (node.domain || "exemplo.com")
  const unsubscribeUrl = node.domain
    ? `https://${node.domain}/unsubscribe?email=${recipient}&node_id=${node.id}`
    : "https://dominio/exemplo/unsubscribe?email=destinatario@exemplo.com"
  const now = new Date()
  const dataStr = now.toLocaleDateString("pt-BR")
  const horaStr = now.toLocaleTimeString("pt-BR")
  const subject = (template?.subject || "Assunto da campanha")
    .replaceAll("{{email}}", recipient)
    .replaceAll("{{domain}}", recipient.includes("@") ? recipient.split("@")[1] : domain)
    .replaceAll("{{protocol}}", protocol)
    .replaceAll("{{data}}", dataStr)
    .replaceAll("{{date}}", dataStr)
    .replaceAll("{{hora}}", horaStr)
    .replaceAll("{{time}}", horaStr)
    .replaceAll("{{cta_url}}", ctaUrl || "https://exemplo.com")
  const hasHtml = Boolean(template?.html)
  const hasText = Boolean(template?.plain_text)
  const contentType = hasHtml && hasText
    ? 'multipart/alternative; boundary="boundary_preview"'
    : hasHtml
      ? "text/html; charset=UTF-8"
      : "text/plain; charset=UTF-8"

  return [
    `From: ${fromAddress}`,
    `To: ${recipient}`,
    `Subject: ${subject}`,
    `Message-ID: <preview-${protocol}@${domain}>`,
    `Return-Path: <${fromAddress}>`,
    `List-ID: <newsletter.${domain}>`,
    `List-Unsubscribe: <${unsubscribeUrl}>`,
    `List-Unsubscribe-Post: List-Unsubscribe=One-Click`,
    `Feedback-ID: task-preview:${domain}:node${node.id}:goog`,
    `MIME-Version: 1.0`,
    `Content-Type: ${contentType}`,
  ]
}

export default function Campaigns({ nodes }) {
  const [templates, setTemplates] = useState([])
  const [lists, setLists] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [progress, setProgress] = useState({}) // campaignId â†’ progress data
  const [selectedCampaignId, setSelectedCampaignId] = useState(null)
  const [editingCampaignId, setEditingCampaignId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")

  const [form, setForm] = useState({
    name: "",
    template_id: "",
    subjects: [""],      // A/B subject list
    sender_name: "",    // Display name in From header
    cta_url: "",
    rate_per_hour: 120,
    chunk_size: 2000,
    node_ids: [],
    list_id: "",
    recipientsText: "",
    test_recipient: "",
  })

  useEffect(() => {
    loadAll()
    const id = setInterval(loadCampaigns, 7000)
    return () => clearInterval(id)
  }, [])

  async function loadAll() {
    setLoading(true)
    try {
      const [tplRes, campaignRes, listRes] = await Promise.all([
        fetch("/api/email/templates"),
        fetch("/api/campaigns"),
        fetch("/api/recipient-lists"),
      ])
      if (tplRes.ok) setTemplates(await tplRes.json())
      if (listRes.ok) setLists(await listRes.json())
      if (campaignRes.ok) {
        const nextCampaigns = await campaignRes.json()
        setCampaigns(nextCampaigns)
        // Auto-fetch progress for active campaigns on first load
        nextCampaigns.forEach(c => {
          if (c.status === "running" || c.status === "paused") fetchProgress(c.id)
        })
        setSelectedCampaignId((current) => {
          if (current && nextCampaigns.some((item) => item.id === current)) return current
          return null
        })
      }
    } finally {
      setLoading(false)
    }
  }

  async function loadCampaigns() {
    const res = await fetch("/api/campaigns")
    if (res.ok) {
      const nextCampaigns = await res.json()
      setCampaigns(nextCampaigns)
      // Refresh progress for running AND paused campaigns
      nextCampaigns.forEach(c => {
        if (c.status === "running" || c.status === "paused") fetchProgress(c.id)
      })
      setSelectedCampaignId((current) => {
        if (current && nextCampaigns.some((item) => item.id === current)) return current
        return null
      })
    }
  }

  async function fetchProgress(campaignId) {
    try {
      const res = await fetch(`/api/campaigns/${campaignId}/progress`)
      if (res.ok) {
        const data = await res.json()
        setProgress(prev => ({ ...prev, [campaignId]: data }))
      }
    } catch {}
  }

  async function handleLaunch(campaignId) {
    if (!form.list_id) return setError("Selecione uma lista de destinatários para lançar")
    if (!form.node_ids.length) return setError("Selecione pelo menos uma VPS para lançar")
    setSending(true)
    setError("")
    try {
      const res = await fetch(`/api/campaigns/${campaignId}/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_ids: form.node_ids,
          list_id: Number(form.list_id),
          chunk_size: Number(form.chunk_size) || 2000,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Erro ao lançar campanha")
      setMessage(`Campanha lançada! ${data.total_recipients?.toLocaleString()} destinatários em ${data.shards} shards.`)
      await loadCampaigns()
      fetchProgress(campaignId)
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  async function handlePause(campaignId) {
    await fetch(`/api/campaigns/${campaignId}/pause`, { method: "POST" })
    loadCampaigns()
  }

  async function handleResume(campaignId) {
    await fetch(`/api/campaigns/${campaignId}/resume`, { method: "POST" })
    loadCampaigns()
  }

  async function handleDeleteCampaign(campaignId) {
    if (!confirm("Remover esta campanha e todas as suas tarefas?")) return
    await fetch(`/api/campaigns/${campaignId}`, { method: "DELETE" })
    loadCampaigns()
  }

  const selectedNodes = useMemo(
    () => nodes.filter((node) => form.node_ids.includes(node.id)),
    [nodes, form.node_ids]
  )
  const recipients = useMemo(() => parseRecipients(form.recipientsText), [form.recipientsText])
  const template = useMemo(
    () => templates.find((item) => item.id === Number(form.template_id)),
    [templates, form.template_id]
  )
  const perNodeCounts = splitCounts(recipients.length, selectedNodes.length)

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function toggleNode(nodeId) {
    setForm((current) => ({
      ...current,
      node_ids: current.node_ids.includes(nodeId)
        ? current.node_ids.filter((id) => id !== nodeId)
        : [...current.node_ids, nodeId],
    }))
  }

  function handleTemplateChange(value) {
    setForm((current) => ({
      ...current,
      template_id: value,
    }))
  }

  async function handleRecipientsFile(event) {
    const file = event.target.files?.[0]
    if (!file) return
    const text = await file.text()
    updateField("recipientsText", text)
    event.target.value = ""
  }

  function resetDraft() {
    setForm({
      name: "",
      template_id: "",
      subjects: [""],
      sender_name: "",
      cta_url: "",
      rate_per_hour: 120,
      chunk_size: 2000,
      node_ids: [],
      list_id: "",
      recipientsText: "",
      test_recipient: "",
    })
    setEditingCampaignId(null)
    setError("")
    setMessage("Rascunho limpo.")
  }

  function openDraftEditor(parentCampaign, draftCampaign) {
    setSelectedCampaignId(parentCampaign?.id || draftCampaign.parent_campaign_id || null)
    setEditingCampaignId(draftCampaign.id)
    // Parse saved subjects from JSON
    let parsedSubjects = [""]
    try {
      const parsed = JSON.parse(draftCampaign.subjects || "[]")
      if (Array.isArray(parsed) && parsed.length > 0) parsedSubjects = parsed
    } catch {}
    setForm({
      name: draftCampaign.name || "",
      template_id: draftCampaign.template_id ? String(draftCampaign.template_id) : "",
      subjects: parsedSubjects,
      sender_name: draftCampaign.sender_name || "",
      cta_url: draftCampaign.cta_url || "",
      rate_per_hour: draftCampaign.rate_per_hour || 120,
      chunk_size: draftCampaign.chunk_size || 2000,
      node_ids: [],
      list_id: draftCampaign.list_id ? String(draftCampaign.list_id) : "",
      recipientsText: "",
      test_recipient: draftCampaign.test_recipient || "",
    })
    setError("")
    setMessage(`Editando rascunho: ${draftCampaign.name}`)
  }

  async function submit(kind) {
    setError("")
    setMessage("")

    const isDraft = kind === "draft"
    const isTest = kind === "test"
    const isLaunch = kind === "launch"

    // Build subjects list: filter out empty entries, fallback to template subject
    const subjectsList = (form.subjects || []).map(s => s.trim()).filter(Boolean)

    const payload = {
      name: form.name.trim() || "Campanha",
      parent_campaign_id: selectedCampaignId,
      template_id: form.template_id ? Number(form.template_id) : null,
      subject: subjectsList[0] || (template?.subject || "").trim(),
      subjects: subjectsList,
      sender_name: form.sender_name.trim() || null,
      cta_url: form.cta_url.trim(),
      rate_per_hour: Number(form.rate_per_hour) || 0,
      chunk_size: Number(form.chunk_size) || 2000,
      scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : null,
      window_start: form.window_start.trim() || null,
      window_end: form.window_end.trim() || null,
      node_ids: form.node_ids,
      recipients,
      test_recipient: form.test_recipient.trim(),
      is_draft: isDraft,
      list_id: form.list_id ? Number(form.list_id) : null,
    }

    if (!payload.template_id) return setError("Selecione um template")
    if (!isDraft && !payload.subject && subjectsList.length === 0) return setError("Assunto obrigatorio")
    if (!isDraft && !payload.node_ids.length) return setError("Selecione pelo menos uma VPS")
    if (isTest && !payload.test_recipient) return setError("Email de teste obrigatorio")
    if (isLaunch && !payload.list_id) return setError("Selecione uma lista de destinatários")
    if (!isTest && !isDraft && !isLaunch && !payload.recipients.length) return setError("Importe ou cole os destinatarios")

    isTest ? setTesting(true) : setSending(true)
    try {
      // For "launch": create/update draft first, then call /launch
      if (isLaunch) {
        let campaignId = editingCampaignId
        if (!campaignId) {
          // Create a new draft first
          const createRes = await fetch("/api/campaigns", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, is_draft: true }),
          })
          const createData = await createRes.json()
          if (!createRes.ok) throw new Error(createData.detail || "Erro ao criar campanha")
          campaignId = createData.campaign_id
          setEditingCampaignId(campaignId)
        }
        // Now launch it
        await handleLaunch(campaignId)
        return
      }

      const url = isTest
        ? "/api/campaigns/test"
        : editingCampaignId && isDraft
          ? `/api/campaigns/${editingCampaignId}`
          : "/api/campaigns"
      const method = isTest ? "POST" : editingCampaignId && isDraft ? "PUT" : "POST"
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || "Erro ao criar campanha")
      if (isTest) {
        const successes = (data.results || []).filter((item) => item.success).length
        const failures = (data.results || []).length - successes
        setMessage(
          failures === 0
            ? `Teste enviado com sucesso para ${successes} VPS.`
            : `Teste concluído com ${successes} sucesso(s) e ${failures} falha(s).`
        )
      } else if (isDraft) {
        setMessage(editingCampaignId ? "Rascunho atualizado." : "Campanha salva como rascunho.")
        setEditingCampaignId(data.campaign_id || null)
        if (!editingCampaignId) resetDraft()
      } else {
        setMessage("Campanha criada com sucesso.")
        setEditingCampaignId(null)
      }
      await loadCampaigns()
    } catch (err) {
      setError(err.message)
    } finally {
      setTesting(false)
      setSending(false)
    }
  }

  return (
    <div style={{ display: "grid", gap: 22 }}>
      <div style={{ display: "grid", gap: 14, padding: 18, background: "#161b22", border: "1px solid #30363d", borderRadius: 10 }}>
        {editingCampaignId && (
          <div style={{ padding: "8px 10px", borderRadius: 8, background: "#0d2238", border: "1px solid #58a6ff", color: "#c9d1d9", fontSize: "0.82em" }}>
            Editando rascunho salvo.
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "2fr 2fr 1.2fr", gap: 12 }}>
          <input
            value={form.name}
            onChange={(e) => updateField("name", e.target.value)}
            placeholder="Nome da campanha"
            style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9" }}
          />
          <select
            value={form.template_id}
            onChange={(e) => handleTemplateChange(e.target.value)}
            style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9" }}
          >
            <option value="">Selecionar template</option>
            {templates.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
          <input
            value={form.rate_per_hour}
            onChange={(e) => updateField("rate_per_hour", e.target.value)}
            placeholder="Emails/hora"
            type="number"
            min="0"
            style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9" }}
          />
        </div>

        {/* Subjects (A/B) + Sender Name */}
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
          <div>
            <div style={{ fontSize: "0.78em", color: "#8b949e", marginBottom: 6, fontWeight: 600 }}>Assuntos (A/B — os agentes alternarão entre eles)</div>
            {(form.subjects || [""]).map((subj, idx) => (
              <div key={idx} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                <input
                  value={subj}
                  onChange={(e) => {
                    const updated = [...(form.subjects || [""])]
                    updated[idx] = e.target.value
                    updateField("subjects", updated)
                  }}
                  placeholder={idx === 0 ? "Assunto principal (ou use o do template)" : `Assunto alternativo ${idx + 1}`}
                  style={{ flex: 1, padding: 10, borderRadius: 6, border: `1px solid ${idx === 0 ? "#30363d" : "#1f6feb"}`, background: "#0d1117", color: "#c9d1d9", fontSize: "0.9em" }}
                />
                {(form.subjects || []).length > 1 && (
                  <button
                    type="button"
                    onClick={() => updateField("subjects", (form.subjects || []).filter((_, i) => i !== idx))}
                    style={{ padding: "6px 10px", borderRadius: 6, background: "#2d1616", color: "#f85149", border: "1px solid #f85149", cursor: "pointer", fontSize: "0.85em" }}
                  >✕</button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={() => updateField("subjects", [...(form.subjects || [""]), ""])}
              style={{ fontSize: "0.78em", padding: "4px 10px", borderRadius: 6, background: "#0d2238", color: "#58a6ff", border: "1px solid #1f6feb", cursor: "pointer" }}
            >+ Adicionar assunto A/B</button>
          </div>
          <div>
            <div style={{ fontSize: "0.78em", color: "#8b949e", marginBottom: 6, fontWeight: 600 }}>Nome do Remetente (opcional)</div>
            <input
              value={form.sender_name}
              onChange={(e) => updateField("sender_name", e.target.value)}
              placeholder='Ex: "Equipe de Vendas" — aparece como Nome &lt;email@dominio&gt;'
              style={{ width: "100%", padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9", boxSizing: "border-box", fontSize: "0.9em" }}
            />
            {form.sender_name && (
              <div style={{ fontSize: "0.72em", color: "#58a6ff", marginTop: 4 }}>Prévia: "{form.sender_name}" &lt;email@dominio.com&gt;</div>
            )}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1.2fr", gap: 12 }}>
          <div style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: template?.subject ? "#c9d1d9" : "#6e7681", minHeight: 42, display: "flex", alignItems: "center", fontSize: "0.85em" }}>
            <span style={{ color: "#6e7681", marginRight: 6 }}>Template:</span> {template?.subject || "Assunto vem do template selecionado"}
          </div>
          <input
            value={form.cta_url}
            onChange={(e) => updateField("cta_url", e.target.value)}
            placeholder="CTA URL — ex: https://site.com/form?email={{email}}"
            style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9" }}
          />
        </div>

        <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: "0.82em", color: "#8b949e", fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
            <span>⏰</span> Agendamento & Janela de Horários (Fuso Brasília UTC-3)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr", gap: 12, alignItems: "center" }}>
            <label style={{ fontSize: "0.78em", color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>
              Agendar Início (opcional)
              <input
                type="datetime-local"
                value={form.scheduled_at}
                onChange={(e) => updateField("scheduled_at", e.target.value)}
                style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9", fontSize: "0.88em" }}
              />
            </label>
            <label style={{ fontSize: "0.78em", color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>
              Janela Início
              <input
                type="time"
                value={form.window_start}
                onChange={(e) => updateField("window_start", e.target.value)}
                placeholder="08:00"
                style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9", fontSize: "0.88em" }}
              />
            </label>
            <label style={{ fontSize: "0.78em", color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>
              Janela Fim
              <input
                type="time"
                value={form.window_end}
                onChange={(e) => updateField("window_end", e.target.value)}
                placeholder="18:00"
                style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9", fontSize: "0.88em" }}
              />
            </label>
          </div>
        </div>

        <div>
          <div style={{ fontSize: "0.85em", color: "#8b949e", marginBottom: 8 }}>VPS para envio</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
            {nodes.map((node) => {
              const checked = form.node_ids.includes(node.id)
              const online = node.agent_status === "online"
              return (
                <label key={node.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: 10, borderRadius: 8, border: checked ? "1px solid #58a6ff" : "1px solid #30363d", background: checked ? "#0d2238" : "#0d1117", opacity: online ? 1 : 0.65 }}>
                  <input type="checkbox" checked={checked} onChange={() => toggleNode(node.id)} />
                  <div>
                    <div style={{ fontWeight: 600 }}>{node.hostname}</div>
                    <div style={{ fontSize: "0.78em", color: online ? "#3fb950" : "#f0883e" }}>
                      {online ? "agent online" : (node.agent_status || "sem agente")}
                    </div>
                    <div style={{ fontSize: "0.75em", color: "#8b949e" }}>{node.email_from || "sem from"}</div>
                  </div>
                </label>
              )
            })}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1.2fr", gap: 12 }}>
          <div>
            <div style={{ fontSize: "0.82em", color: "#8b949e", marginBottom: 6 }}>📋 Lista de destinatários</div>
            <select
              value={form.list_id}
              onChange={(e) => updateField("list_id", e.target.value)}
              style={{ width: "100%", padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: form.list_id ? "#c9d1d9" : "#6e7681" }}
            >
              <option value="">Selecionar lista (para envio em massa)</option>
              {lists.map((list) => (
                <option key={list.id} value={list.id}>
                  {list.name} — {list.active_count.toLocaleString()} ativos
                </option>
              ))}
            </select>
            {form.list_id && (() => {
              const sel = lists.find(l => String(l.id) === String(form.list_id))
              return sel ? (
                <div style={{ marginTop: 6, fontSize: "0.78em", color: "#8b949e" }}>
                  ✅ {sel.active_count.toLocaleString()} ativos · 🚫 {(sel.total_count - sel.active_count).toLocaleString()} inativos
                  {form.node_ids.length > 0 && (
                    <span> · ~{Math.ceil(sel.active_count / form.node_ids.length).toLocaleString()} por VPS</span>
                  )}
                </div>
              ) : null
            })()}
          </div>
          <div>
            <div style={{ fontSize: "0.82em", color: "#8b949e", marginBottom: 6 }}>Chunk size (emails por tarefa)</div>
            <select
              value={form.chunk_size}
              onChange={(e) => updateField("chunk_size", Number(e.target.value))}
              style={{ width: "100%", padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9" }}
            >
              <option value={500}>500 — alta granularidade</option>
              <option value={1000}>1.000</option>
              <option value={2000}>2.000 — padrão</option>
              <option value={5000}>5.000</option>
              <option value={10000}>10.000 — menos roundtrips</option>
            </select>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1.2fr", gap: 12 }}>
          <div>
            <div style={{ fontSize: "0.82em", color: "#8b949e", marginBottom: 6 }}>Ou cole/importe destinatários (envio rápido)</div>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
              <label style={{ fontSize: "0.8em", color: "#58a6ff", cursor: "pointer" }}>
                Importar arquivo
                <input type="file" accept=".txt,.csv" onChange={handleRecipientsFile} style={{ display: "none" }} />
              </label>
            </div>
            <textarea
              value={form.recipientsText}
              onChange={(e) => updateField("recipientsText", e.target.value)}
              placeholder={"um@email.com\noutro@email.com"}
              style={{ width: "100%", minHeight: 150, padding: 10, borderRadius: 8, border: "1px solid #30363d", background: "#0d1117", color: "#c9d1d9", resize: "vertical" }}
            />
          </div>
          <div style={{ padding: 12, borderRadius: 8, border: "1px solid #30363d", background: "#0d1117" }}>
            <div style={{ marginBottom: 10, fontWeight: 600 }}>Resumo</div>
            <div style={{ fontSize: "0.85em", color: "#8b949e", lineHeight: 1.8 }}>
              <div>Total de emails: <strong style={{ color: "#c9d1d9" }}>{recipients.length}</strong></div>
              <div>VPS selecionadas: <strong style={{ color: "#c9d1d9" }}>{selectedNodes.length}</strong></div>
            </div>
            <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
              {selectedNodes.map((node, index) => (
                <div key={node.id} style={{ fontSize: "0.8em", color: "#8b949e" }}>
                  {node.hostname}: <strong style={{ color: "#c9d1d9" }}>{perNodeCounts[index] || 0}</strong>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid #30363d" }}>
              <div style={{ fontSize: "0.8em", color: "#8b949e", fontWeight: 600, marginBottom: 8 }}>Headers por VPS</div>
              {selectedNodes.length === 0 && (
                <div style={{ fontSize: "0.78em", color: "#8b949e" }}>Selecione ao menos uma VPS para ver os headers.</div>
              )}
              <div style={{ display: "grid", gap: 10 }}>
                {selectedNodes.map((node) => (
                  <div key={node.id} style={{ padding: 10, borderRadius: 8, border: "1px solid #30363d", background: "#161b22" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <strong style={{ fontSize: "0.82em" }}>{node.hostname}</strong>
                      <span style={{ marginLeft: "auto", fontSize: "0.72em", color: "#8b949e" }}>{node.email_from || "sem from"}</span>
                    </div>
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "0.72em", lineHeight: 1.55, color: "#c9d1d9" }}>
                      {buildHeaderPreview(
                        node,
                        template,
                        form.test_recipient.trim() || recipients[0] || "destinatario@exemplo.com",
                        form.cta_url.trim()
                      ).join("\n")}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
            <input
              value={form.test_recipient}
              onChange={(e) => updateField("test_recipient", e.target.value)}
              placeholder="email de teste"
              style={{ width: "100%", marginTop: 14, padding: 9, borderRadius: 6, border: "1px solid #30363d", background: "#161b22", color: "#c9d1d9" }}
            />
          </div>
        </div>

        {(error || message) && (
          <div style={{ padding: "10px 12px", borderRadius: 8, background: error ? "#3d2424" : "#0f2d18", color: error ? "#f85149" : "#3fb950" }}>
            {error || message}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button onClick={() => submit("draft")} disabled={sending || testing} style={{ padding: "10px 16px", borderRadius: 6, border: "1px solid #30363d", background: "#2d333b", color: "white", cursor: sending || testing ? "not-allowed" : "pointer", fontWeight: 600 }}>
            Salvar rascunho
          </button>
          <button onClick={resetDraft} disabled={sending || testing} style={{ padding: "10px 16px", borderRadius: 6, border: "1px solid #30363d", background: "transparent", color: "#8b949e", cursor: sending || testing ? "not-allowed" : "pointer", fontWeight: 600 }}>
            Limpar formulário
          </button>
          <button onClick={() => submit("test")} disabled={testing || sending} style={{ padding: "10px 16px", borderRadius: 6, border: "1px solid #30363d", background: "#1f6feb", color: "white", cursor: testing || sending ? "not-allowed" : "pointer", fontWeight: 600 }}>
            {testing ? "Testando..." : "✉ Testar SMTPs"}
          </button>
          {recipients.length > 0 && !form.list_id && (
            <button onClick={() => submit("send")} disabled={sending || testing} style={{ padding: "10px 16px", borderRadius: 6, border: "none", background: "#6e40c9", color: "white", cursor: sending || testing ? "not-allowed" : "pointer", fontWeight: 600 }}>
              {sending ? "Criando..." : `⚡ Envio rápido (${recipients.length} emails)`}
            </button>
          )}
          <button onClick={() => submit("launch")} disabled={sending || testing || !form.list_id} title={!form.list_id ? "Selecione uma lista para lançar" : ""} style={{ padding: "10px 18px", borderRadius: 6, border: "none", background: form.list_id ? "#238636" : "#21262d", color: form.list_id ? "white" : "#6e7681", cursor: form.list_id && !sending && !testing ? "pointer" : "not-allowed", fontWeight: 700, fontSize: "0.95em" }}>
            {sending ? "Lançando..." : "🚀 Lançar campanha"}
          </button>
        </div>
      </div>

      <div style={{ padding: 18, background: "#161b22", border: "1px solid #30363d", borderRadius: 10 }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
          <h3 style={{ margin: 0 }}>Campanhas</h3>
          <button onClick={loadCampaigns} style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: 6, border: "1px solid #30363d", background: "transparent", color: "#8b949e", cursor: "pointer" }}>
            Atualizar
          </button>
        </div>

        {loading && <div style={{ color: "#8b949e" }}>Carregando...</div>}
        {!loading && campaigns.length === 0 && <div style={{ color: "#8b949e" }}>Nenhuma campanha criada ainda.</div>}

        {!loading && campaigns.length > 0 && (
          <div style={{ display: "grid", gap: 12 }}>
            {campaigns.map((campaign) => {
              const active = campaign.id === selectedCampaignId
              const prog = progress[campaign.id]
              const isRunning = campaign.status === "running"
              const isPaused = campaign.status === "paused"

              return (
                <div
                  key={campaign.id}
                  style={{
                    padding: 14,
                    borderRadius: 12,
                    border: active ? "1px solid #58a6ff" : "1px solid #30363d",
                    background: active ? "#0d2238" : "#0d1117",
                  }}
                >
                  <button
                    onClick={() => (campaign.is_draft ? openDraftEditor(null, campaign) : setSelectedCampaignId(campaign.id))}
                    style={{ width: "100%", textAlign: "left", padding: 0, border: "none", background: "transparent", color: "inherit", cursor: "pointer" }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <strong style={{ fontSize: "1em" }}>{campaign.name}</strong>
                      <span style={{ fontSize: "0.72em", color: campaign.is_draft ? "#58a6ff" : campaign.status === "done" ? "#3fb950" : campaign.status === "running" ? "#d29922" : campaign.status === "paused" ? "#8b949e" : campaign.status === "failed" ? "#f85149" : "#d29922" }}>
                        {campaign.is_draft ? "rascunho" : campaign.is_test ? "teste" : campaign.status || "campanha"}
                      </span>
                      <span style={{ marginLeft: "auto", fontSize: "0.75em", color: "#8b949e" }}>
                        {new Date(campaign.created_at).toLocaleString("pt-BR")}
                      </span>
                    </div>
                    <div style={{ marginTop: 6, fontSize: "0.82em", color: "#8b949e", lineHeight: 1.6 }}>
                      <div>{campaign.subject || "Sem assunto"}</div>
                      <div>{campaign.sent_count}/{campaign.total_recipients} enviados · {campaign.error_count} erros</div>
                    </div>
                  </button>

                  {/* Progress bars for sharded campaigns */}
                  {prog && prog.type === "sharded" && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                        <ProgressBar value={prog.sent} total={prog.total} color={prog.status === "paused" ? "#6e7681" : "#238636"} height={8} />
                        <span style={{ fontSize: "0.78em", color: "#8b949e", minWidth: 50 }}>{prog.pct}%</span>
                        <span style={{ fontSize: "0.75em", color: "#8b949e" }}>{prog.sent?.toLocaleString()}/{prog.total?.toLocaleString()}</span>
                      </div>
                      <div style={{ display: "grid", gap: 4 }}>
                        {prog.shards.map(shard => (
                          <div key={shard.shard_id} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.75em" }}>
                            <span style={{ color: "#8b949e", minWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{shard.node_name}</span>
                            <ProgressBar value={shard.sent} total={shard.total} color={shard.status === "done" ? "#3fb950" : shard.status === "paused" ? "#6e7681" : "#1f6feb"} height={5} />
                            <span style={{ color: "#8b949e", minWidth: 36 }}>{shard.pct}%</span>
                            <span style={{ color: shard.errors > 0 ? "#f85149" : "#8b949e", minWidth: 50 }}>
                              {shard.errors > 0 ? `âš?  err` : `âœ“ ${shard.status}`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Controls for running/paused campaigns */}
                  {(isRunning || isPaused) && (
                    <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
                      <button
                        onClick={() => fetchProgress(campaign.id)}
                        style={{ fontSize: "0.75em", padding: "3px 10px", background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, cursor: "pointer" }}
                      >
                        ↻ Progresso
                      </button>
                      {isRunning && (
                        <button
                          onClick={() => handlePause(campaign.id)}
                          style={{ fontSize: "0.75em", padding: "3px 10px", background: "#3d2424", color: "#f0883e", border: "1px solid #f0883e", borderRadius: 4, cursor: "pointer" }}
                        >
                          â¸ Pausar
                        </button>
                      )}
                      {isPaused && (
                        <button
                          onClick={() => handleResume(campaign.id)}
                          style={{ fontSize: "0.75em", padding: "3px 10px", background: "#0d2e0d", color: "#3fb950", border: "1px solid #3fb950", borderRadius: 4, cursor: "pointer" }}
                        >
                          ▶ Retomar
                        </button>
                      )}
                    </div>
                  )}

                  <div style={{ marginTop: 12, borderTop: "1px solid #30363d", paddingTop: 10, display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ fontSize: "0.8em", color: "#8b949e", fontWeight: 600 }}>Sub-itens</div>
                    <button
                      onClick={() => handleDeleteCampaign(campaign.id)}
                      style={{ marginLeft: "auto", fontSize: "0.72em", padding: "2px 8px", background: "transparent", color: "#f85149", border: "1px solid #f85149", borderRadius: 4, cursor: "pointer" }}
                    >
                      🗑 Remover
                    </button>
                  </div>
                  {(campaign.children || []).length === 0 && (
                    <div style={{ fontSize: "0.8em", color: "#8b949e", marginTop: 6 }}>Nenhum rascunho ou teste ainda.</div>
                  )}
                  {(campaign.children || []).map((child) => (
                    <button
                      key={child.id}
                      onClick={() => child.is_draft && openDraftEditor(campaign, child)}
                      style={{ width: "100%", textAlign: "left", padding: 10, borderRadius: 8, border: "1px solid #30363d", background: "#161b22", color: "inherit", cursor: child.is_draft ? "pointer" : "default", marginTop: 6 }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <strong style={{ fontSize: "0.85em" }}>{child.name}</strong>
                        <span style={{ marginLeft: "auto", fontSize: "0.72em", color: child.is_draft ? "#58a6ff" : child.is_test ? "#d29922" : "#8b949e" }}>
                          {child.is_draft ? "rascunho" : child.is_test ? "teste" : "execução"}
                        </span>
                      </div>
                      <div style={{ marginTop: 4, fontSize: "0.76em", color: "#8b949e", lineHeight: 1.5 }}>
                        <div>{child.subject || "Sem assunto"}</div>
                        <div>{new Date(child.created_at).toLocaleString("pt-BR")}</div>
                      </div>
                    </button>
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
