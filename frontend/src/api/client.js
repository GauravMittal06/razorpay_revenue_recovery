import axios from "axios";

const BASE_URL = "http://127.0.0.1:8000";

export async function fetchMetrics() {
  const res = await axios.get(`${BASE_URL}/api/metrics`);
  return res.data;
}

export async function fetchCases(filters = {}) {
  const params = {};
  if (filters.event_type) params.event_type = filters.event_type;
  if (filters.recovery_status) params.recovery_status = filters.recovery_status;
  if (filters.outcome) params.outcome = filters.outcome;

  const res = await axios.get(`${BASE_URL}/api/cases`, { params });
  return res.data;
}

export async function fetchCaseDetail(paymentId) {
  const res = await axios.get(`${BASE_URL}/api/cases/${paymentId}`);
  return res.data;
}

export async function triggerEvent(payload) {
  const res = await axios.post(`${BASE_URL}/api/events/trigger`, payload);
  return res.data;
}

export async function submitReply(paymentId, message) {
  const res = await axios.post(`${BASE_URL}/api/cases/${paymentId}/reply`, { message });
  return res.data;
}

export async function simulateRecovery(paymentId) {
  const res = await axios.post(`${BASE_URL}/api/cases/${paymentId}/simulate-recovery`);
  return res.data;
}

export async function fetchAuditFeed(limit = 20) {
  const res = await axios.get(`${BASE_URL}/api/audit-feed`, { params: { limit } });
  return res.data;
}