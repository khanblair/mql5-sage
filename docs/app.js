// mql5-sage | Dashboard JavaScript

async function loadStatus() {
    try {
        const response = await fetch('status.json');
        const data = await response.json();
        renderDashboard(data);
    } catch (error) {
        console.error('Failed to load status:', error);
        document.body.innerHTML = `
            <div class="container" style="text-align: center; padding-top: 4rem;">
                <h1>⚠️ Unable to load status</h1>
                <p style="color: var(--text-secondary); margin-top: 1rem;">
                    The dashboard requires GitHub Pages to be enabled.<br>
                    Go to Settings → Pages → Source: Deploy from a branch
                </p>
            </div>
        `;
    }
}

function renderDashboard(data) {
    // Update hero stats
    document.getElementById('coveragePct').textContent = data.coverage_pct + '%';
    document.getElementById('pageCount').textContent = data.page_count.toLocaleString();
    document.getElementById('chunkCount').textContent = data.chunk_count.toLocaleString();
    document.getElementById('queryCount').textContent = data.query_count.toLocaleString();
    document.getElementById('journalCount').textContent = data.journal_count.toLocaleString();
    document.getElementById('sectionCount').textContent = `${data.sections_with_pages}/${data.total_sections}`;
    document.getElementById('lastUpdated').textContent = data.last_updated;

    // Update coverage ring
    const ring = document.getElementById('coverageRing');
    const circumference = 2 * Math.PI * 52; // r=52
    const offset = circumference - (data.coverage_pct / 100) * circumference;
    ring.style.strokeDashoffset = offset;

    // Render sections
    renderSections(data.sections);

    // Render recent journal
    renderJournal(data.recent_journal);
}

function renderSections(sections) {
    const container = document.getElementById('sectionsList');
    const sorted = Object.entries(sections).sort((a, b) => b[1] - a[1]);

    container.innerHTML = sorted.map(([slug, pages]) => {
        const pct = Math.min(Math.round(pages / 60 * 100), 100);
        let status = 'not-started';
        if (pct >= 100) status = 'complete';
        else if (pct > 0) status = 'in-progress';

        const title = slug.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

        return `
            <div class="section-item ${status}">
                <span class="section-name">${title}</span>
                <span class="section-progress">${pct}% (${pages})</span>
            </div>
        `;
    }).join('');
}

function renderJournal(entries) {
    const container = document.getElementById('activityList');

    if (!entries || entries.length === 0) {
        container.innerHTML = '<p style="color: var(--text-secondary);">No journal entries yet.</p>';
        return;
    }

    container.innerHTML = entries.map(entry => {
        const time = new Date(entry.timestamp).toLocaleDateString('en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        });

        const emoji = { LEARNING: '📚', QUERY: '🔍', EVOLUTION: '🧬', CRAWL: '🕷️', MILESTONE: '🏆' }[entry.entry_type] || '•';

        return `
            <div class="activity-item ${entry.entry_type}">
                <div class="activity-header">
                    <span class="activity-type">${emoji} ${entry.entry_type}</span>
                    <span class="activity-time">${time}</span>
                </div>
                <div class="activity-title">${entry.title}</div>
                <div class="activity-body">${entry.body}</div>
            </div>
        `;
    }).join('');
}

// Initialize
loadStatus();
