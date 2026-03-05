/**
 * AI 產業生態圖 — D3.js Force-Directed Network Graph
 */

(function () {
    // === Data: AI Industry Stocks ===
    const categories = [
        { id: 'core_chip', label: '核心晶片', color: '#a855f7', emoji: '🟣' },
        { id: 'ic_design', label: 'IC 設計', color: '#3b82f6', emoji: '🔵' },
        { id: 'ai_server', label: 'AI 伺服器', color: '#10b981', emoji: '🟢' },
        { id: 'component', label: '散熱/PCB', color: '#f97316', emoji: '🟠' },
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
        // AI Server / Assembly
        { id: '2382', name: '廣達', category: 'ai_server', weight: 7 },
        { id: '3231', name: '緯創', category: 'ai_server', weight: 6 },
        { id: '6669', name: '緯穎', category: 'ai_server', weight: 5 },
        { id: '2356', name: '英業達', category: 'ai_server', weight: 4 },
        { id: '4938', name: '和碩', category: 'ai_server', weight: 4 },
        // Components / PCB / Thermal
        { id: '2308', name: '台達電', category: 'component', weight: 6 },
        { id: '3037', name: '欣興', category: 'component', weight: 5 },
        { id: '8046', name: '南電', category: 'component', weight: 4 },
        { id: '2327', name: '國巨', category: 'component', weight: 3 },
        // Packaging / Memory
        { id: '3711', name: '日月光', category: 'packaging', weight: 6 },
        { id: '6239', name: '力成', category: 'packaging', weight: 4 },
        { id: '3260', name: '威剛', category: 'packaging', weight: 3 },
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
    ];

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
        const height = Math.max(450, Math.min(width * 0.7, 600));

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
            .force('charge', d3.forceManyBody().strength(-350))
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
            tooltip.html(`<strong>${d.id} ${d.name}</strong><br>${categoryMap[d.category].emoji} ${categoryMap[d.category].label}<br><span style="color:#94a3b8;font-size:11px;">點擊查看深度分析</span>`)
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
