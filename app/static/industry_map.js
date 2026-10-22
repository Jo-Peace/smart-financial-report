/**
 * AI 產業生態圖 — D3.js Force-Directed Network Graph
 */

(function () {
    // === Data: AI Industry Stocks ===
    const categories = [
        { id: 'core_chip', label: '核心晶片', color: '#a855f7', emoji: '🟣' },
        { id: 'ic_design', label: 'IC 設計', color: '#3b82f6', emoji: '🔵' },
        { id: 'ai_server', label: 'AI 伺服器', color: '#10b981', emoji: '🟢' },
        { id: 'component', label: '散熱/電源', color: '#f97316', emoji: '🟠' },
        { id: 'pcb', label: 'PCB/載板/CCL', color: '#f472b6', emoji: '🩷' },
        { id: 'cpo', label: 'CPO/矽光子', color: '#22d3ee', emoji: '🩵' },
        { id: 'chassis', label: '機殼滑軌/連接器', color: '#84cc16', emoji: '🟩' },
        { id: 'equipment', label: '半導體設備', color: '#94a3b8', emoji: '⚪' },
        { id: 'packaging', label: '封測/記憶體', color: '#ef4444', emoji: '🔴' },
        { id: 'brand', label: '品牌/終端', color: '#eab308', emoji: '🟡' },
    ];

    const categoryMap = {};
    categories.forEach(c => categoryMap[c.id] = c);

    const nodes = [
        // Core chips
        { id: '2330', name: '台積電', category: 'core_chip', weight: 10 },
        { id: '2454', name: '聯發科', category: 'core_chip', weight: 7 },
        // IC Design
        { id: '2379', name: '瑞昱', category: 'ic_design', weight: 4 },
        { id: '3034', name: '聯詠', category: 'ic_design', weight: 4 },
        { id: '3661', name: '世芯KY', category: 'ic_design', weight: 5 },
        { id: '3443', name: '創意', category: 'ic_design', weight: 4 },
        { id: '5274', name: '信驊', category: 'ic_design', weight: 4 },
        { id: '3035', name: '智原', category: 'ic_design', weight: 3 },
        // AI Server / Assembly
        { id: '2382', name: '廣達', category: 'ai_server', weight: 7 },
        { id: '3231', name: '緯創', category: 'ai_server', weight: 6 },
        { id: '6669', name: '緯穎', category: 'ai_server', weight: 5 },
        { id: '2356', name: '英業達', category: 'ai_server', weight: 4 },
        { id: '4938', name: '和碩', category: 'ai_server', weight: 4 },
        // Components / PCB / Thermal
        { id: '2308', name: '台達電', category: 'component', weight: 6 },
        { id: '3017', name: '奇鋐', category: 'component', weight: 5 },
        { id: '3324', name: '雙鴻', category: 'component', weight: 4 },
        { id: '3653', name: '健策', category: 'component', weight: 4 },
        // PCB / substrate / CCL
        { id: '3037', name: '欣興', category: 'pcb', weight: 5 },
        { id: '8046', name: '南電', category: 'pcb', weight: 4 },
        { id: '4958', name: '臻鼎-KY', category: 'pcb', weight: 4 },
        { id: '2383', name: '台光電', category: 'pcb', weight: 5 },
        { id: '2368', name: '金像電', category: 'pcb', weight: 4 },
        { id: '6213', name: '聯茂', category: 'pcb', weight: 3 },
        { id: '1802', name: '台玻', category: 'pcb', weight: 3 },
        // CPO / silicon photonics
        { id: '6442', name: '光聖', category: 'cpo', weight: 4 },
        { id: '3363', name: '上詮', category: 'cpo', weight: 3 },
        { id: '3450', name: '聯鈞', category: 'cpo', weight: 3 },
        { id: '4979', name: '華星光', category: 'cpo', weight: 3 },
        // Chassis / rails / connectors / passives
        { id: '2059', name: '川湖', category: 'chassis', weight: 4 },
        { id: '8210', name: '勤誠', category: 'chassis', weight: 3 },
        { id: '3665', name: '貿聯-KY', category: 'chassis', weight: 4 },
        { id: '3533', name: '嘉澤', category: 'chassis', weight: 4 },
        { id: '2327', name: '國巨', category: 'chassis', weight: 3 },
        // Semiconductor equipment
        { id: '6187', name: '萬潤', category: 'equipment', weight: 3 },
        { id: '3583', name: '辛耘', category: 'equipment', weight: 3 },
        // Packaging / Memory
        { id: '3711', name: '日月光', category: 'packaging', weight: 6 },
        { id: '6239', name: '力成', category: 'packaging', weight: 4 },
        { id: '3260', name: '威剛', category: 'packaging', weight: 3 },
        { id: '2408', name: '南亞科', category: 'packaging', weight: 4 },
        { id: '2344', name: '華邦電', category: 'packaging', weight: 4 },
        { id: '2449', name: '京元電子', category: 'packaging', weight: 4 },
        // Brand / Terminal
        { id: '2317', name: '鴻海', category: 'brand', weight: 8 },
        { id: '2357', name: '華碩', category: 'brand', weight: 4 },
        { id: '2376', name: '技嘉', category: 'brand', weight: 4 },
        { id: '2377', name: '微星', category: 'brand', weight: 3 },
    ];

    // Links: supply chain relationships
    const links = [
        // TSMC → everyone in IC design (foundry)
        { source: '2330', target: '2454', strength: 0.8 },
        { source: '2330', target: '3661', strength: 0.6 },
        { source: '2330', target: '3443', strength: 0.6 },
        { source: '2330', target: '3034', strength: 0.4 },
        { source: '2330', target: '2379', strength: 0.4 },
        // TSMC → packaging
        { source: '2330', target: '3711', strength: 0.7 },
        { source: '2330', target: '6239', strength: 0.5 },
        // MediaTek → brands
        { source: '2454', target: '2317', strength: 0.5 },
        // Server assemblers ↔ core chips
        { source: '2382', target: '2330', strength: 0.7 },
        { source: '3231', target: '2330', strength: 0.6 },
        { source: '6669', target: '2382', strength: 0.7 },
        { source: '2356', target: '2330', strength: 0.4 },
        { source: '4938', target: '2330', strength: 0.4 },
        // Server assemblers ↔ brands  
        { source: '2382', target: '2317', strength: 0.5 },
        { source: '3231', target: '2317', strength: 0.5 },
        // Components → servers & chips
        { source: '2308', target: '2382', strength: 0.5 },
        { source: '2308', target: '3231', strength: 0.4 },
        { source: '3037', target: '2330', strength: 0.5 },
        { source: '3037', target: '3711', strength: 0.4 },
        { source: '8046', target: '2330', strength: 0.5 },
        { source: '8046', target: '3037', strength: 0.4 },
        { source: '2327', target: '2308', strength: 0.3 },
        // Brands internal
        { source: '2357', target: '2454', strength: 0.4 },
        { source: '2376', target: '2454', strength: 0.3 },
        { source: '2377', target: '2454', strength: 0.3 },
        // Memory ↔ server
        { source: '3260', target: '2382', strength: 0.3 },
        { source: '3260', target: '3231', strength: 0.3 },
        // Expanded supply chain
        { source: '3017', target: '2382', strength: 0.5 },
        { source: '3324', target: '3231', strength: 0.4 },
        { source: '3653', target: '3017', strength: 0.3 },
        { source: '2383', target: '2368', strength: 0.5 },
        { source: '2383', target: '6213', strength: 0.3 },
        { source: '1802', target: '2383', strength: 0.4 },
        { source: '2368', target: '2382', strength: 0.4 },
        { source: '4958', target: '3037', strength: 0.4 },
        { source: '5274', target: '6669', strength: 0.5 },
        { source: '5274', target: '2330', strength: 0.4 },
        { source: '3035', target: '2330', strength: 0.4 },
        { source: '2408', target: '3260', strength: 0.4 },
        { source: '2344', target: '2408', strength: 0.3 },
        { source: '2449', target: '2330', strength: 0.4 },
        { source: '2449', target: '3711', strength: 0.3 },
        { source: '6442', target: '3363', strength: 0.4 },
        { source: '3363', target: '2330', strength: 0.4 },
        { source: '3450', target: '6442', strength: 0.3 },
        { source: '4979', target: '6442', strength: 0.3 },
        { source: '2059', target: '2382', strength: 0.4 },
        { source: '8210', target: '6669', strength: 0.4 },
        { source: '3665', target: '2382', strength: 0.4 },
        { source: '3533', target: '3231', strength: 0.4 },
        { source: '6187', target: '3711', strength: 0.4 },
        { source: '3583', target: '2330', strength: 0.4 },
    ];

    // Local daily-analysis roles (data/stock_db.json market_role): direction vs elastic.
    const roleLabels = { direction_indicator: '方向指標股', elastic_attack: '彈性攻擊股', theme_confirmation: '題材確認股' };
    const nodeRoles = {
        '2330': 'direction_indicator', '2317': 'direction_indicator', '2454': 'direction_indicator',
        '2382': 'theme_confirmation', '2368': 'theme_confirmation', '2449': 'theme_confirmation',
        '3450': 'theme_confirmation', '8210': 'theme_confirmation',
        '4958': 'elastic_attack', '3017': 'elastic_attack', '2408': 'elastic_attack',
        '2344': 'elastic_attack', '1802': 'elastic_attack', '6213': 'elastic_attack',
    };

    // === Build Legend ===
    function buildLegend() {
        const legend = document.getElementById('mapLegend');
        if (!legend) return;
        legend.innerHTML = categories.map(c =>
            `<span class="legend-item">
                <span class="legend-dot" style="background: ${c.color}"></span>
                ${c.label}
            </span>`
        ).join('');
    }

    // === Build Graph ===
    function buildGraph() {
        const container = document.getElementById('industryMapContainer');
        const svg = d3.select('#industryMapSvg');
        if (!container || svg.empty()) return;

        const width = container.clientWidth;
        const height = Math.max(560, Math.min(width * 0.8, 760));

        svg.attr('width', width).attr('height', height);
        svg.selectAll('*').remove();

        // Zoom
        const g = svg.append('g');
        svg.call(d3.zoom()
            .scaleExtent([0.4, 3])
            .on('zoom', (event) => g.attr('transform', event.transform))
        );

        // Tooltip
        let tooltip = d3.select('#mapTooltip');
        if (tooltip.empty()) {
            tooltip = d3.select('body').append('div')
                .attr('id', 'mapTooltip')
                .attr('class', 'map-tooltip')
                .style('opacity', 0);
        }

        // Node radius scale
        const radiusScale = d3.scaleSqrt()
            .domain([3, 10])
            .range([18, 42]);

        // Force simulation
        const simulation = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(links).id(d => d.id).distance(d => 120 - d.strength * 40).strength(d => d.strength * 0.3))
            .force('charge', d3.forceManyBody().strength(-260))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(d => radiusScale(d.weight) + 8))
            .force('x', d3.forceX(width / 2).strength(0.06))
            .force('y', d3.forceY(height / 2).strength(0.06));

        // Draw links
        const link = g.append('g')
            .selectAll('line')
            .data(links)
            .join('line')
            .attr('class', 'map-link')
            .attr('stroke-width', d => 0.5 + d.strength * 2)
            .attr('stroke-opacity', d => 0.15 + d.strength * 0.25);

        // Draw nodes
        const node = g.append('g')
            .selectAll('g')
            .data(nodes)
            .join('g')
            .attr('class', 'map-node')
            .style('cursor', 'pointer')
            .call(d3.drag()
                .on('start', dragstarted)
                .on('drag', dragged)
                .on('end', dragended)
            );

        // Node circles
        node.append('circle')
            .attr('r', d => radiusScale(d.weight))
            .attr('fill', d => categoryMap[d.category].color)
            .attr('fill-opacity', 0.25)
            .attr('stroke', d => categoryMap[d.category].color)
            .attr('stroke-width', 2)
            .attr('stroke-opacity', 0.8);

        // Inner glow circle
        node.append('circle')
            .attr('r', d => radiusScale(d.weight) * 0.5)
            .attr('fill', d => categoryMap[d.category].color)
            .attr('fill-opacity', 0.5);

        // Node labels (stock code)
        node.append('text')
            .attr('class', 'map-node-code')
            .attr('text-anchor', 'middle')
            .attr('dy', '-0.1em')
            .attr('font-size', d => d.weight >= 7 ? '11px' : '9px')
            .attr('font-weight', '700')
            .attr('fill', '#f1f5f9')
            .text(d => d.id);

        // Node labels (stock name)
        node.append('text')
            .attr('class', 'map-node-name')
            .attr('text-anchor', 'middle')
            .attr('dy', '1.1em')
            .attr('font-size', d => d.weight >= 7 ? '10px' : '8px')
            .attr('font-weight', '500')
            .attr('fill', '#94a3b8')
            .text(d => d.name);

        // Hover effects
        node.on('mouseover', function (event, d) {
            d3.select(this).select('circle').transition().duration(200)
                .attr('fill-opacity', 0.45)
                .attr('stroke-width', 3);

            tooltip.transition().duration(200).style('opacity', 1);
            tooltip.html(`<strong>${d.id} ${d.name}</strong><br>${categoryMap[d.category].emoji} ${categoryMap[d.category].label}${nodeRoles[d.id] ? ' · ' + roleLabels[nodeRoles[d.id]] : ''}<br><span style="color:#94a3b8;font-size:11px;">點擊查看深度分析</span>`)
                .style('left', (event.pageX + 12) + 'px')
                .style('top', (event.pageY - 28) + 'px');

            // Highlight connected links
            link.attr('stroke-opacity', l =>
                (l.source.id === d.id || l.target.id === d.id) ? 0.8 : 0.08
            ).attr('stroke', l =>
                (l.source.id === d.id || l.target.id === d.id) ? categoryMap[d.category].color : '#1e3a5f'
            );
        }).on('mouseout', function (event, d) {
            d3.select(this).select('circle').transition().duration(200)
                .attr('fill-opacity', 0.25)
                .attr('stroke-width', 2);

            tooltip.transition().duration(300).style('opacity', 0);

            link.attr('stroke-opacity', l => 0.15 + l.strength * 0.25)
                .attr('stroke', '#1e3a5f');
        }).on('click', function (event, d) {
            // Trigger stock analysis
            if (typeof quickSearch === 'function') {
                window.scrollTo({ top: 0, behavior: 'smooth' });
                setTimeout(() => quickSearch(d.id), 300);
            }
        });

        // Simulation tick
        simulation.on('tick', () => {
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            node.attr('transform', d => {
                d.x = Math.max(40, Math.min(width - 40, d.x));
                d.y = Math.max(40, Math.min(height - 40, d.y));
                return `translate(${d.x},${d.y})`;
            });
        });

        // Drag handlers
        function dragstarted(event, d) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }

        function dragged(event, d) {
            d.fx = event.x;
            d.fy = event.y;
        }

        function dragended(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }
    }

    // === Init ===
    document.addEventListener('DOMContentLoaded', () => {
        buildLegend();
        buildGraph();
    });

    // Resize handler
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(buildGraph, 300);
    });
})();
