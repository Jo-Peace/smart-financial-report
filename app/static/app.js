/**
 * AI 個股研究員 — Frontend Logic
 */

// === State ===
let isLoading = false;

// === Init ===
document.addEventListener('DOMContentLoaded', () => {
    updateQuota();

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
        const el = document.getElementById('quotaValue');
        el.textContent = `${data.remaining}/${data.total}`;

        el.classList.remove('warning', 'empty');
        if (data.remaining === 0) {
            el.classList.add('empty');
        } else if (data.remaining === 1) {
            el.classList.add('warning');
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
