/**
 * AI 個股研究員 — Frontend Logic
 */

// === State ===
let isLoading = false;

// === Init ===
document.addEventListener('DOMContentLoaded', () => {
    updateQuota();
    loadHotStocks();
    loadRecentReports();

    // Enter key to search
    document.getElementById('tickerInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleSearch();
    });

    // Auto-focus
    document.getElementById('tickerInput').focus();
});


// === Quota ===
async function updateQuota() {
    try {
        const res = await fetch('/api/quota');
        const data = await res.json();
        let noQuota = false;
        let quotaMessage = '';

        // Personal quota
        const el = document.getElementById('quotaValue');
        el.textContent = `${data.remaining}/${data.total}`;
        el.classList.remove('warning', 'empty');
        if (data.remaining === 0) {
            el.classList.add('empty');
            noQuota = true;
            quotaMessage = '個人額度已滿';
        } else if (data.remaining === 1) {
            el.classList.add('warning');
        }

        // Global quota
        const globalEl = document.getElementById('globalQuotaValue');
        if (globalEl && data.global_remaining !== undefined) {
            globalEl.textContent = `${data.global_remaining}/${data.global_total}`;
            globalEl.classList.remove('warning', 'empty');
            if (data.global_remaining === 0) {
                globalEl.classList.add('empty');
                noQuota = true;
                quotaMessage = '全站額度已滿';
            } else if (data.global_remaining <= Math.max(5, data.global_total * 0.1)) {
                globalEl.classList.add('warning');
            }
        }

        // Disable UI if out of quota
        const searchBtn = document.getElementById('searchBtn');
        const tickerInput = document.getElementById('tickerInput');
        if (noQuota) {
            if (searchBtn) {
                searchBtn.disabled = true;
                const btnText = searchBtn.querySelector('.btn-text');
                if (btnText) btnText.textContent = quotaMessage;
                searchBtn.style.backgroundColor = '#4a5568';
                searchBtn.style.color = '#a0aec0';
                searchBtn.style.cursor = 'not-allowed';
            }
            if (tickerInput) {
                tickerInput.disabled = true;
                tickerInput.placeholder = `今日${quotaMessage}，請明日再來`;
            }
        }

    } catch (e) {
        console.error('Quota check failed:', e);
    }
}


// === Search ===
function quickSearch(ticker) {
    document.getElementById('tickerInput').value = ticker;
    handleSearch();
}

async function handleSearch() {
    if (isLoading) return;

    const ticker = document.getElementById('tickerInput').value.trim();
    if (!ticker) {
        shakeInput();
        return;
    }

    isLoading = true;
    showLoading(ticker);

    try {
        const res = await fetch('/api/research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: ticker })
        });

        const data = await res.json();

        if (!res.ok) {
            showError(data.error || '未知錯誤', data.message || '');
            return;
        }

        showReport(data);
    } catch (e) {
        showError('網路連線失敗', '請確認伺服器是否運行中');
    } finally {
        isLoading = false;
        updateQuota();
    }
}


// === Loading Animation ===
function showLoading(ticker) {
    hideAll();
    document.getElementById('loadingSection').style.display = 'block';
    document.getElementById('loadingTicker').textContent = ticker;

    // Animate steps
    const steps = ['step1', 'step2', 'step3', 'step4'];
    const delays = [0, 3000, 8000, 15000];

    steps.forEach((id, i) => {
        const el = document.getElementById(id);
        el.classList.remove('active', 'done');

        setTimeout(() => {
            if (!isLoading) return;
            // Mark previous as done
            for (let j = 0; j < i; j++) {
                document.getElementById(steps[j]).classList.remove('active');
                document.getElementById(steps[j]).classList.add('done');
                document.getElementById(steps[j]).textContent = '✅ ' +
                    document.getElementById(steps[j]).textContent.replace('✅ ', '');
            }
            el.classList.add('active');
        }, delays[i]);
    });
}


// === Report Display ===
function showReport(data) {
    hideAll();
    document.getElementById('reportSection').style.display = 'block';
    document.getElementById('reportTicker').textContent = data.ticker;
    document.getElementById('reportName').textContent = data.name || '';

    const badge = document.getElementById('reportBadge');
    if (data.cached) {
        badge.textContent = '📦 快取';
        badge.className = 'report-badge cached';
    } else {
        badge.textContent = '✨ 即時生成';
        badge.className = 'report-badge fresh';
    }

    // Render markdown
    const content = document.getElementById('reportContent');
    if (typeof marked !== 'undefined') {
        content.innerHTML = marked.parse(data.content || '');

        // Post-process blockquotes for GitHub-style alerts
        const blockquotes = content.querySelectorAll('blockquote');
        blockquotes.forEach(bq => {
            const firstP = bq.querySelector('p');
            if (firstP) {
                if (firstP.innerHTML.startsWith('[!TIP]')) {
                    firstP.innerHTML = firstP.innerHTML.replace('[!TIP]', '').trim();
                    const alertDiv = document.createElement('div');
                    alertDiv.className = 'custom-alert alert-tip';
                    alertDiv.innerHTML = `<div class="alert-icon">💡</div><div class="alert-text">${bq.innerHTML}</div>`;
                    bq.parentNode.replaceChild(alertDiv, bq);
                } else if (firstP.innerHTML.startsWith('[!WARNING]')) {
                    firstP.innerHTML = firstP.innerHTML.replace('[!WARNING]', '').trim();
                    const alertDiv = document.createElement('div');
                    alertDiv.className = 'custom-alert alert-warning';
                    alertDiv.innerHTML = `<div class="alert-icon">⚠️</div><div class="alert-text">${bq.innerHTML}</div>`;
                    bq.parentNode.replaceChild(alertDiv, bq);
                }
            }
        });
    } else {
        content.innerHTML = '<pre>' + (data.content || '') + '</pre>';
    }

    // Scroll to report
    document.getElementById('reportSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
}


// === Error Display ===
function showError(title, message) {
    hideAll();
    document.getElementById('errorSection').style.display = 'block';
    document.getElementById('errorTitle').textContent = title;
    document.getElementById('errorMessage').textContent = message;
}


// === Navigation ===
function resetToSearch() {
    hideAll();
    document.getElementById('heroSection').style.display = 'block';
    document.getElementById('tickerInput').value = '';
    document.getElementById('tickerInput').focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function hideAll() {
    document.getElementById('heroSection').style.display = 'none';
    document.getElementById('loadingSection').style.display = 'none';
    document.getElementById('errorSection').style.display = 'none';
    document.getElementById('reportSection').style.display = 'none';
}


// === Utils ===
function shakeInput() {
    const box = document.querySelector('.search-box');
    box.style.animation = 'shake 0.4s ease';
    setTimeout(() => { box.style.animation = ''; }, 400);
}

// Shake animation (added via JS since it's a one-off)
const style = document.createElement('style');
style.textContent = `
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        25% { transform: translateX(-8px); }
        75% { transform: translateX(8px); }
    }
`;
document.head.appendChild(style);

// === Recent Reports ===
async function loadRecentReports() {
    try {
        const res = await fetch('/api/recent');
        const data = await res.json();
        const container = document.getElementById('recentReportsContainer');
        if (!container) return;
        
        container.innerHTML = '';
        
        if (!data || data.length === 0) {
            container.innerHTML = '<span style="color: var(--text-secondary); font-size: 0.9rem;">目前尚無近期紀錄</span>';
            return;
        }

        data.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'hint-chip';
            btn.style.border = '1px solid var(--border-color)';
            
            // Highlight today's reports
            const twDate = new Date(new Date().getTime() + 8 * 3600 * 1000).toISOString().split('T')[0];
            const isToday = item.date === twDate;
            const badge = isToday ? '<span style="color: #48bb78; margin-left:4px; font-weight:bold;">🆕</span>' : '';
            
            btn.innerHTML = `<strong>${item.ticker}</strong> ${item.name} <span style="font-size: 0.75rem; opacity: 0.8; margin-left: 4px;">(${item.date.slice(5)})</span>${badge}`;
            btn.onclick = () => quickSearch(item.ticker);
            container.appendChild(btn);
        });
    } catch (e) {
        console.error('Failed to load recent reports:', e);
    }
}

// === Hot Stocks ===
async function loadHotStocks() {
    try {
        const res = await fetch('/api/hot_stocks');
        const data = await res.json();
        const container = document.getElementById('hotStocksWrapper');
        if (!container) return;
        
        container.innerHTML = '';
        
        if (!data || data.length === 0) {
            container.innerHTML = '<span style="color: #64748b; font-size: 0.9rem;">目前尚無精選資料</span>';
            return;
        }

        data.forEach(item => {
            const btn = document.createElement('button');
            // Using inline styling for immediate visual impact without extra CSS edits
            btn.style.background = 'linear-gradient(135deg, rgba(239,68,68,0.1) 0%, rgba(249,115,22,0.1) 100%)';
            btn.style.border = '1px solid rgba(239,68,68,0.3)';
            btn.style.color = '#f87171';
            btn.style.padding = '8px 12px';
            btn.style.borderRadius = '8px';
            btn.style.fontSize = '0.9rem';
            btn.style.cursor = 'pointer';
            btn.style.display = 'flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '6px';
            btn.style.transition = 'all 0.2s ease';
            
            btn.onmouseover = () => {
                btn.style.background = 'linear-gradient(135deg, rgba(239,68,68,0.2) 0%, rgba(249,115,22,0.2) 100%)';
                btn.style.transform = 'translateY(-1px)';
            };
            btn.onmouseout = () => {
                btn.style.background = 'linear-gradient(135deg, rgba(239,68,68,0.1) 0%, rgba(249,115,22,0.1) 100%)';
                btn.style.transform = 'none';
            };
            
            // Format views with commas
            const formattedViews = new Intl.NumberFormat().format(item.views);
            
            // Format date (remove year for cleaner look)
            const displayDate = item.date.length > 5 ? item.date.substring(5) : item.date;
            
            btn.innerHTML = `
                <strong style="font-size:1rem;">${item.ticker}</strong> 
                <span>${item.name}</span>
                <div style="background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin-left: 4px; display:flex; gap: 6px;">
                    <span title="累積觀看次數">👁️ ${formattedViews}</span>
                    <span style="opacity: 0.6;">|</span>
                    <span title="資料更新日期">📅 ${displayDate}</span>
                </div>
            `;
            
            btn.onclick = () => quickSearch(item.ticker);
            container.appendChild(btn);
        });
    } catch (e) {
        console.error('Failed to load hot stocks:', e);
    }
}

