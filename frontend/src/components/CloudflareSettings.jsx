import { useEffect, useState } from "react"
import {
  addCloudflareAccount,
  deleteCloudflareAccount,
  deleteCloudflareAccount as _deleteAccount,
  importSelectedZones,
  listCloudflareAccounts,
  listCloudflareDomainRecords,
  listCloudflareDomains,
  previewCloudflareZones,
  deleteCloudflareDomainRecord,
} from "../api"

export default function CloudflareSettings() {
  const [loading, setLoading] = useState(true)
  const [accounts, setAccounts] = useState([])
  const [domains, setDomains] = useState([])
  const [status, setStatus] = useState(null)

  // Add-account form
  const [showAddForm, setShowAddForm] = useState(false)
  const [newName, setNewName] = useState("")
  const [newToken, setNewToken] = useState("")
  const [adding, setAdding] = useState(false)

  // Preview modal
  const [previewModal, setPreviewModal] = useState(null) // { accountId, zones: [], selected: Set }
  const [importing, setImporting] = useState(false)

  // Domain records expansion
  const [openDomainId, setOpenDomainId] = useState(null)
  const [loadingRecordsId, setLoadingRecordsId] = useState(null)
  const [recordsByDomainId, setRecordsByDomainId] = useState({})
  const [deletingRecordId, setDeletingRecordId] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const [accs, doms] = await Promise.all([listCloudflareAccounts(), listCloudflareDomains()])
      setAccounts(accs || [])
      setDomains(doms || [])
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleAddAccount() {
    if (!newToken.trim()) return setStatus({ ok: false, message: "Cole o API Token para continuar." })
    if (!newName.trim()) return setStatus({ ok: false, message: "Dê um nome para identificar a conta." })
    setAdding(true)
    setStatus(null)
    try {
      await addCloudflareAccount({ name: newName.trim(), api_token: newToken.trim() })
      // Load account list fresh then immediately fetch preview
      const accs = await listCloudflareAccounts()
      setAccounts(accs)
      setNewName("")
      setNewToken("")
      setShowAddForm(false)
      setStatus({ ok: true, message: "Conta adicionada! Agora escolha quais domínios importar." })
      // Open preview for the new account
      const newAcc = accs[accs.length - 1]
      if (newAcc) handlePreviewZones(newAcc.id)
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setAdding(false)
    }
  }

  async function handlePreviewZones(accountId) {
    setStatus(null)
    try {
      const data = await previewCloudflareZones(accountId)
      setPreviewModal({
        accountId,
        zones: data.zones || [],
        selected: new Set((data.zones || []).filter(z => !z.already_imported).map(z => z.id)),
      })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    }
  }

  async function handleImportSelected() {
    if (!previewModal) return
    const zoneIds = [...previewModal.selected]
    if (zoneIds.length === 0) return setStatus({ ok: false, message: "Selecione pelo menos um domínio." })
    setImporting(true)
    try {
      const result = await importSelectedZones(previewModal.accountId, zoneIds)
      setStatus({ ok: true, message: `Importados: ${result.created} novos, ${result.updated} atualizados.` })
      const [accs, doms] = await Promise.all([listCloudflareAccounts(), listCloudflareDomains()])
      setAccounts(accs)
      setDomains(doms)
      setPreviewModal(null)
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setImporting(false)
    }
  }

  async function handleDeleteAccount(id, name) {
    if (!window.confirm(`Remover a conta "${name}" e todos os domínios associados? Domínios vinculados a VPS não serão removidos.`)) return
    setStatus(null)
    try {
      await deleteCloudflareAccount(id)
      const [accs, doms] = await Promise.all([listCloudflareAccounts(), listCloudflareDomains()])
      setAccounts(accs)
      setDomains(doms)
      setStatus({ ok: true, message: `Conta "${name}" removida.` })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    }
  }

  async function loadDomainRecords(domainId) {
    setLoadingRecordsId(domainId)
    try {
      const data = await listCloudflareDomainRecords(domainId)
      setRecordsByDomainId(prev => ({ ...prev, [domainId]: data.records || [] }))
    } finally {
      setLoadingRecordsId(null)
    }
  }

  async function toggleDomainRecords(domainId) {
    if (openDomainId === domainId) { setOpenDomainId(null); return }
    setOpenDomainId(domainId)
    if (!recordsByDomainId[domainId]) await loadDomainRecords(domainId)
  }

  async function handleDeleteRecord(domainId, record) {
    if (!window.confirm(`Excluir o registro ${record.type} ${record.name}?`)) return
    setDeletingRecordId(record.id)
    setStatus(null)
    try {
      await deleteCloudflareDomainRecord(domainId, record.id)
      await loadDomainRecords(domainId)
      setStatus({ ok: true, message: `Registro excluído: ${record.type} ${record.name}.` })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setDeletingRecordId(null)
    }
  }

  if (loading) return <p style={{ color: "#8b949e" }}>Carregando configuração Cloudflare...</p>

  return (
    <div className="add-node-form" style={{ maxWidth: 820 }}>
      <h2>Domínios Cloudflare</h2>
      <p className="full-width" style={{ color: "#8b949e", fontSize: "0.9em", margin: "0 0 12px 0" }}>
        Adicione múltiplas contas Cloudflare e escolha quais domínios importar de cada uma.
      </p>

      {status && (
        <div className={`full-width section-result ${status.ok ? "status-ok" : "status-err"}`} style={{ marginBottom: 12 }}>
          {status.message}
        </div>
      )}

      {/* Accounts list */}
      <div className="full-width" style={{ display: "grid", gap: 10 }}>
        {accounts.length === 0 && !showAddForm && (
          <p className="node-card-meta">Nenhuma conta Cloudflare cadastrada.</p>
        )}
        {accounts.map(acc => (
          <div key={acc.id} style={{ border: "1px solid #30363d", borderRadius: 10, background: "#0d1117", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
                  <span>☁️</span> {acc.name}
                  <span style={{ fontSize: "0.72em", padding: "2px 8px", borderRadius: 10, background: "#0d2238", color: "#58a6ff" }}>
                    {acc.domain_count} {acc.domain_count === 1 ? "domínio" : "domínios"}
                  </span>
                </div>
                <div className="node-card-meta" style={{ marginTop: 2 }}>Adicionada em {new Date(acc.created_at).toLocaleDateString("pt-BR")}</div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => handlePreviewZones(acc.id)} style={{ fontSize: "0.82em", padding: "4px 10px" }}>
                  🌐 Importar domínios
                </button>
                <button
                  onClick={() => handleDeleteAccount(acc.id, acc.name)}
                  style={{ fontSize: "0.82em", padding: "4px 10px", background: "#2d1616", color: "#ffb3b3" }}
                >
                  Remover conta
                </button>
              </div>
            </div>

            {/* Domains for this account */}
            {(() => {
              const accountDomains = domains.filter(d => d.account_id === acc.id)
              if (accountDomains.length === 0) return null
              return (
                <div style={{ borderTop: "1px solid #21262d", padding: "8px 14px 10px", display: "grid", gap: 6 }}>
                  {accountDomains.map(item => (
                    <div key={item.id}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, border: "1px solid #21262d", borderRadius: 7, padding: "6px 10px", background: "#161b22" }}>
                        <div>
                          <div style={{ fontWeight: 600, fontSize: "0.9em" }}>{item.domain}</div>
                          <div className="node-card-meta" style={{ marginTop: 2 }}>zone: {item.zone_id || "auto"}</div>
                        </div>
                        <button onClick={() => toggleDomainRecords(item.id)} style={{ fontSize: "0.78em", padding: "3px 8px" }}>
                          {openDomainId === item.id ? "▲ Ocultar DNS" : "▼ Ver DNS"}
                        </button>
                      </div>
                      {openDomainId === item.id && (
                        <div style={{ marginTop: 4, border: "1px solid #21262d", borderRadius: 7, padding: "8px 10px", background: "#11161c" }}>
                          {loadingRecordsId === item.id ? (
                            <div className="node-card-meta">Carregando registros...</div>
                          ) : (recordsByDomainId[item.id] || []).length === 0 ? (
                            <div className="node-card-meta">Nenhum registro encontrado.</div>
                          ) : (
                            <div style={{ display: "grid", gap: 6 }}>
                              {(recordsByDomainId[item.id] || []).map(r => (
                                <div key={r.id} style={{ fontSize: "0.82em", fontFamily: "monospace", border: "1px solid #2a3340", borderRadius: 6, padding: "6px 8px", background: "#0d1117" }}>
                                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                                    <div>
                                      <div><strong>{r.type}</strong> {r.name}</div>
                                      <div style={{ color: "#9fb3c8", wordBreak: "break-all" }}>{r.content}</div>
                                      <div style={{ color: "#7f8c9b", marginTop: 2 }}>
                                        ttl: {r.ttl}{r.priority ? ` · priority: ${r.priority}` : ""}{typeof r.proxied === "boolean" ? ` · proxied: ${r.proxied ? "true" : "false"}` : ""}
                                      </div>
                                    </div>
                                    <button
                                      onClick={() => handleDeleteRecord(item.id, r)}
                                      disabled={deletingRecordId === r.id}
                                      style={{ background: "#2d1616", color: "#ffb3b3", whiteSpace: "nowrap", fontSize: "0.82em", padding: "3px 8px" }}
                                    >
                                      {deletingRecordId === r.id ? "Excluindo..." : "Excluir"}
                                    </button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )
            })()}
          </div>
        ))}

        {/* Unlinked domains (no account_id) */}
        {(() => {
          const unlinked = domains.filter(d => !d.account_id)
          if (unlinked.length === 0) return null
          return (
            <div style={{ border: "1px dashed #30363d", borderRadius: 10, padding: "10px 14px" }}>
              <div style={{ fontSize: "0.82em", color: "#8b949e", marginBottom: 8, fontWeight: 600 }}>Domínios legados (sem conta associada)</div>
              <div style={{ display: "grid", gap: 6 }}>
                {unlinked.map(item => (
                  <div key={item.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, border: "1px solid #21262d", borderRadius: 7, padding: "6px 10px", background: "#161b22" }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: "0.9em" }}>{item.domain}</div>
                      <div className="node-card-meta">zone: {item.zone_id || "auto"}</div>
                    </div>
                    <button onClick={() => toggleDomainRecords(item.id)} style={{ fontSize: "0.78em", padding: "3px 8px" }}>
                      {openDomainId === item.id ? "▲ Ocultar DNS" : "▼ Ver DNS"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )
        })()}
      </div>

      {/* Add account form */}
      {showAddForm ? (
        <div className="full-width" style={{ border: "1px solid #1f6feb", borderRadius: 10, padding: 14, background: "#0d1117", display: "grid", gap: 10 }}>
          <div style={{ fontWeight: 600, color: "#58a6ff" }}>Nova conta Cloudflare</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 10 }}>
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="Nome da conta (ex: Conta Principal)"
              style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#161b22", color: "#c9d1d9" }}
            />
            <input
              type="password"
              value={newToken}
              onChange={e => setNewToken(e.target.value)}
              placeholder="API Token com Zone:DNS:Edit"
              style={{ padding: 10, borderRadius: 6, border: "1px solid #30363d", background: "#161b22", color: "#c9d1d9" }}
            />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={handleAddAccount} disabled={adding}>
              {adding ? "Validando e salvando..." : "✓ Salvar conta"}
            </button>
            <button onClick={() => { setShowAddForm(false); setNewName(""); setNewToken("") }} style={{ background: "#2d1616", color: "#ffb3b3" }}>
              Cancelar
            </button>
          </div>
        </div>
      ) : (
        <div className="full-width">
          <button onClick={() => setShowAddForm(true)} style={{ background: "#0d2238", color: "#58a6ff", border: "1px solid #1f6feb" }}>
            + Adicionar conta Cloudflare
          </button>
        </div>
      )}

      {/* Zone preview modal */}
      {previewModal && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          onClick={e => { if (e.target === e.currentTarget) setPreviewModal(null) }}
        >
          <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 14, padding: 24, maxWidth: 600, width: "90%", maxHeight: "80vh", overflow: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h3 style={{ margin: 0 }}>Selecionar domínios para importar</h3>
              <button onClick={() => setPreviewModal(null)} style={{ background: "transparent", color: "#8b949e", border: "none", fontSize: "1.2em", cursor: "pointer" }}>✕</button>
            </div>
            <div style={{ fontSize: "0.82em", color: "#8b949e", marginBottom: 12 }}>
              {previewModal.zones.length} zone(s) encontrada(s). Selecione as que deseja importar.
            </div>

            {/* Select all / deselect all */}
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <button
                onClick={() => setPreviewModal(pm => ({ ...pm, selected: new Set(pm.zones.map(z => z.id)) }))}
                style={{ fontSize: "0.78em", padding: "3px 10px" }}
              >Selecionar todas</button>
              <button
                onClick={() => setPreviewModal(pm => ({ ...pm, selected: new Set() }))}
                style={{ fontSize: "0.78em", padding: "3px 10px", background: "#2d1616", color: "#ffb3b3" }}
              >Desmarcar todas</button>
            </div>

            <div style={{ display: "grid", gap: 6, maxHeight: "50vh", overflowY: "auto" }}>
              {previewModal.zones.map(zone => (
                <label
                  key={zone.id}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 7, border: `1px solid ${previewModal.selected.has(zone.id) ? "#1f6feb" : "#21262d"}`, background: previewModal.selected.has(zone.id) ? "#0d2238" : "#0d1117", cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={previewModal.selected.has(zone.id)}
                    onChange={e => {
                      setPreviewModal(pm => {
                        const next = new Set(pm.selected)
                        e.target.checked ? next.add(zone.id) : next.delete(zone.id)
                        return { ...pm, selected: next }
                      })
                    }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600 }}>{zone.name}</div>
                    <div style={{ fontSize: "0.75em", color: "#6e7681" }}>
                      {zone.status}
                      {zone.already_imported && " · já importado"}
                    </div>
                  </div>
                  {zone.already_imported && (
                    <span style={{ fontSize: "0.68em", padding: "2px 6px", borderRadius: 6, background: "#0f2d18", color: "#3fb950" }}>importado</span>
                  )}
                </label>
              ))}
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button onClick={handleImportSelected} disabled={importing || previewModal.selected.size === 0}>
                {importing ? "Importando..." : `✓ Importar ${previewModal.selected.size} domínio(s)`}
              </button>
              <button onClick={() => setPreviewModal(null)} style={{ background: "#21262d", color: "#8b949e" }}>Cancelar</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
