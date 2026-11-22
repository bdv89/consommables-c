/**
 * Metrics dashboard application
 */
(function() {
  'use strict';

  // DOM elements
  const totalRequestsEl = document.getElementById('total-requests');
  const totalSearchesEl = document.getElementById('total-searches');
  const uniqueIpsTotalEl = document.getElementById('unique-ips-total');
  const uniqueIpsTodayEl = document.getElementById('unique-ips-today');
  const dateRangeEl = document.getElementById('date-range');
  const endpointsTbody = document.getElementById('endpoints-tbody');
  const topSearchesList = document.getElementById('top-searches');
  const dailyTbody = document.getElementById('daily-tbody');

  /**
   * Format number with thousands separator
   */
  function formatNumber(n) {
    return n.toLocaleString('fr-FR');
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * Load and display summary stats
   */
  async function loadSummary() {
    try {
      const resp = await fetch('api/metrics/summary');
      const data = await resp.json();

      // Calculate total requests
      let totalRequests = 0;
      data.endpoints.forEach(e => { totalRequests += e.total; });

      totalRequestsEl.textContent = formatNumber(totalRequests);
      totalSearchesEl.textContent = formatNumber(data.total_searches || 0);
      uniqueIpsTotalEl.textContent = formatNumber(data.unique_ips_total || 0);
      uniqueIpsTodayEl.textContent = formatNumber(data.unique_ips_today || 0);

      // Date range
      if (data.date_range && data.date_range.start) {
        dateRangeEl.textContent = `Donnees depuis le ${data.date_range.start}`;
      }

      // Endpoints table
      endpointsTbody.innerHTML = '';
      data.endpoints.forEach(e => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="endpoint-tag">${escapeHtml(e.endpoint)}</span></td>
          <td>${formatNumber(e.total)}</td>
          <td>${e.last_seen || '-'}</td>
        `;
        endpointsTbody.appendChild(tr);
      });

    } catch (err) {
      console.error('Error loading summary:', err);
    }
  }

  /**
   * Load and display top searches
   */
  async function loadTopSearches() {
    try {
      const resp = await fetch('api/metrics/top-searches?limit=15');
      const data = await resp.json();

      topSearchesList.innerHTML = '';
      if (!data.searches || data.searches.length === 0) {
        topSearchesList.innerHTML = '<li><em>Aucune recherche enregistree</em></li>';
        return;
      }

      data.searches.forEach(s => {
        const li = document.createElement('li');
        li.innerHTML = `
          <span class="query">${escapeHtml(s.query)}</span>
          <span class="count">${formatNumber(s.count)}</span>
        `;
        topSearchesList.appendChild(li);
      });

    } catch (err) {
      console.error('Error loading top searches:', err);
    }
  }

  /**
   * Load and display daily stats
   */
  async function loadDailyStats() {
    try {
      const resp = await fetch('api/metrics/daily');
      const data = await resp.json();

      dailyTbody.innerHTML = '';
      if (!data.totals || data.totals.length === 0) {
        dailyTbody.innerHTML = '<tr><td colspan="3"><em>Aucune donnee</em></td></tr>';
        return;
      }

      // Show last 30 days
      const last30 = data.totals.slice(0, 30);
      last30.forEach(d => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${escapeHtml(d.date)}</td>
          <td>${formatNumber(d.total)}</td>
          <td>${formatNumber(d.unique_ips)}</td>
        `;
        dailyTbody.appendChild(tr);
      });

    } catch (err) {
      console.error('Error loading daily stats:', err);
    }
  }

  /**
   * Initialize dashboard
   */
  async function init() {
    // Load all data in parallel
    await Promise.all([
      loadSummary(),
      loadTopSearches(),
      loadDailyStats()
    ]);
  }

  // Start
  init();

  // Auto-refresh every 60 seconds
  setInterval(init, 60000);

})();
