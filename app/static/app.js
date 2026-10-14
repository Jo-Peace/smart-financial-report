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
    bindStaticHandlers();

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

        // Out of quota: only NEW reports are blocked. Cached reports are still free,
        // so the search stays enabled and the server answers (cache hit, or 429/503
        // with its own message). Only a notice is shown here.
        const tickerInput = document.getElementById('tickerInput');
        if (tickerInput) {
            if (!tickerInput.dataset.defaultPlaceholder) {
                tickerInput.dataset.defaultPlaceholder = tickerInput.placeholder || '';
            }
            tickerInput.placeholder = noQuota
                ? `今日${quotaMessage}：仍可查詢已生成的報告（不扣額度）`
                : tickerInput.dataset.defaultPlaceholder;
            tickerInput.title = noQuota ? `今日${quotaMessage}，新報告明天再產生；已生成的報告仍可免費查閱` : '';
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

    // Render markdown (model output is untrusted: always sanitize before innerHTML)
    const content = document.getElementById('reportContent');
    renderReportContent(content, data.content || '');

    // Scroll to report
    document.getElementById('reportSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
}


// === Safe report rendering ===
function renderReportContent(container, markdown) {
    const canRenderHtml = typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined';
    if (!canRenderHtml) {
        // Fallback: plain text only (never inject raw model output as HTML)
        const pre = document.createElement('pre');
        pre.textContent = markdown;
        container.replaceChildren(pre);
        return;
    }
    container.innerHTML = DOMPurify.sanitize(marked.parse(markdown), { USE_PROFILES: { html: true } });

    // Post-process blockquotes for GitHub-style alerts (DOM moves only; no string re-parsing)
    container.querySelectorAll('blockquote').forEach(bq => {
        const firstP = bq.querySelector('p');
        if (!firstP) return;
        const text = firstP.textContent || '';
        let kind = null;
        if (text.startsWith('[!TIP]')) kind = { cls: 'alert-tip', icon: '💡', tag: '[!TIP]' };
        else if (text.startsWith('[!WARNING]')) kind = { cls: 'alert-warning', icon: '⚠️', tag: '[!WARNING]' };
        if (!kind) return;
        stripLeadingTag(firstP, kind.tag);

        const alertDiv = document.createElement('div');
        alertDiv.className = `custom-alert ${kind.cls}`;
        const icon = document.createElement('div');
        icon.className = 'alert-icon';
        icon.textContent = kind.icon;
        const body = document.createElement('div');
        body.className = 'alert-text';
        while (bq.firstChild) body.appendChild(bq.firstChild);
        alertDiv.append(icon, body);
        bq.replaceWith(alertDiv);
    });
}

function stripLeadingTag(paragraph, tag) {
    const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode();
    if (node && node.nodeValue.trimStart().startsWith(tag)) {
        node.nodeValue = node.nodeValue.trimStart().slice(tag.length).trimStart();
    }
}

function bindStaticHandlers() {
    const searchBtn = document.getElementById('searchBtn');
    if (searchBtn) searchBtn.addEventListener('click', handleSearch);
    document.querySelectorAll('[data-action="reset"]').forEach(btn => {
        btn.addEventListener('click', resetToSearch);
    });
}

function textSpan(text, style) {
    const span = document.createElement('span');
    span.textContent = text;
    if (style) span.style.cssText = style;
    return span;
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
            
            const strong = document.createElement('strong');
            strong.textContent = item.ticker;
            btn.append(strong, ' ', item.name || '');
            btn.appendChild(textSpan(`(${String(item.date || '').slice(5)})`, 'font-size: 0.75rem; opacity: 0.8; margin-left: 4px;'));
            if (isToday) btn.appendChild(textSpan('🆕', 'color: #48bb78; margin-left:4px; font-weight:bold;'));
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
            
            const strong = document.createElement('strong');
            strong.style.fontSize = '1rem';
            strong.textContent = item.ticker;
            btn.append(strong, textSpan(item.name || ''));

            const meta = document.createElement('div');
            meta.style.cssText = 'background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin-left: 4px; display:flex; gap: 6px;';
            if (item.editor_pick) {
                // Cold start: editor picks carry no view count or date (never show invented numbers)
                const pick = textSpan('📌 編輯精選');
                pick.title = '尚無足夠觀看紀錄，由編輯挑選';
                meta.appendChild(pick);
            } else {
                const views = textSpan(`👁️ ${new Intl.NumberFormat().format(item.views || 0)}`);
                views.title = '近 7 日實際觀看次數';
                const date = String(item.date || '');
                const dateSpan = textSpan(`📅 ${date.length > 5 ? date.substring(5) : date}`);
                dateSpan.title = '報告生成日期';
                meta.append(views, textSpan('|', 'opacity: 0.6;'), dateSpan);
            }
            btn.appendChild(meta);

            btn.onclick = () => quickSearch(item.ticker);
            container.appendChild(btn);
        });
    } catch (e) {
        console.error('Failed to load hot stocks:', e);
    }
}

