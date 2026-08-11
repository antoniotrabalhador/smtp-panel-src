const API_BASE = "/api"

export async function listNodes() {
  const res = await fetch(`${API_BASE}/nodes`)
  if (!res.ok) throw new Error("failed to list nodes")
  return res.json()
}

export async function createNode(node) {
  const res = await fetch(`${API_BASE}/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(node),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to create node")
  }
  return res.json()
}

export async function deleteNode(id) {
  const res = await fetch(`${API_BASE}/nodes/${id}`, { method: "DELETE" })
  if (!res.ok) throw new Error("failed to delete node")
}

export async function testSsh(id) {
  const res = await fetch(`${API_BASE}/nodes/${id}/test-ssh`, { method: "POST" })
  return res.json()
}

export async function updateNode(id, patch) {
  const res = await fetch(`${API_BASE}/nodes/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail?.[0]?.msg || err.detail || "failed to update node")
  }
  return res.json()
}

export async function bootstrapNode(id) {
  const res = await fetch(`${API_BASE}/nodes/${id}/bootstrap`, { method: "POST" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to bootstrap node")
  }
  return res.json()
}

export async function getCloudflareConfig() {
  const res = await fetch(`${API_BASE}/cloudflare/config`)
  if (!res.ok) throw new Error("failed to load cloudflare config")
  return res.json()
}

export async function updateCloudflareConfig(payload) {
  const res = await fetch(`${API_BASE}/cloudflare/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to save cloudflare config")
  }
  return res.json()
}

export async function testCloudflareConfig(payload = {}) {
  const res = await fetch(`${API_BASE}/cloudflare/config/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to test cloudflare config")
  }
  return res.json()
}

export async function listCloudflareDomains() {
  const res = await fetch(`${API_BASE}/cloudflare/domains`)
  if (!res.ok) throw new Error("failed to list cloudflare domains")
  return res.json()
}

export async function createCloudflareDomain(payload) {
  const res = await fetch(`${API_BASE}/cloudflare/domains`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to create cloudflare domain")
  }
  return res.json()
}

export async function deleteCloudflareDomain(id) {
  const res = await fetch(`${API_BASE}/cloudflare/domains/${id}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to delete cloudflare domain")
  }
}

export async function listCloudflareZones() {
  const res = await fetch(`${API_BASE}/cloudflare/zones`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to list cloudflare zones")
  }
  return res.json()
}

export async function importCloudflareZones() {
  const res = await fetch(`${API_BASE}/cloudflare/domains/import`, { method: "POST" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to import cloudflare zones")
  }
  return res.json()
}

export async function listCloudflareDomainRecords(domainId) {
  const res = await fetch(`${API_BASE}/cloudflare/domains/${domainId}/records`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to list domain records")
  }
  return res.json()
}

export async function deleteCloudflareDomainRecord(domainId, recordId) {
  const res = await fetch(`${API_BASE}/cloudflare/domains/${domainId}/records/${recordId}`, {
    method: "DELETE",
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to delete domain record")
  }
}

// ── Multi-account Cloudflare ──────────────────────────────────────────────────

export async function listCloudflareAccounts() {
  const res = await fetch(`${API_BASE}/cloudflare/accounts`)
  if (!res.ok) throw new Error("failed to list cloudflare accounts")
  return res.json()
}

export async function addCloudflareAccount(payload) {
  const res = await fetch(`${API_BASE}/cloudflare/accounts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to add cloudflare account")
  }
  return res.json()
}

export async function deleteCloudflareAccount(id) {
  const res = await fetch(`${API_BASE}/cloudflare/accounts/${id}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to delete cloudflare account")
  }
}

export async function previewCloudflareZones(accountId) {
  const res = await fetch(`${API_BASE}/cloudflare/accounts/${accountId}/preview-zones`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to preview cloudflare zones")
  }
  return res.json()
}

export async function importSelectedZones(accountId, zoneIds) {
  const res = await fetch(`${API_BASE}/cloudflare/accounts/${accountId}/import-zones`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zone_ids: zoneIds }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "failed to import zones")
  }
  return res.json()
}
