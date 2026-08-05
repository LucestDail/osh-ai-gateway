(function () {
    var HEALTH_TIMEOUT_MS = 4000;
    var LLM_HEALTH_TIMEOUT_MS = 2500;
    var STATS_REFRESH_MS = 60000;

    var SERVICES = [
        { id: 'osh', health: '/osh/actuator/health', label: 'OSH' },
        { id: 'llm', health: '/llm/health', label: 'LLM Gateway' },
        { id: 'simplestock', health: '/simpleStock/health', label: 'simpleStock' },
        { id: 'aim', health: '/aim/', label: 'AIm' },
        { id: 'myapi', health: '/myapi/', label: 'myapi' },
        { id: 'mycomputer', health: '/my-computer/', label: 'my-computer' },
    ];

    function fetchWithTimeout(url, ms) {
        return new Promise(function (resolve, reject) {
            var timer = setTimeout(function () {
                reject(new Error('timeout'));
            }, ms);
            fetch(url, { method: 'GET', cache: 'no-store', credentials: 'same-origin' })
                .then(function (res) {
                    clearTimeout(timer);
                    resolve(res);
                })
                .catch(function (err) {
                    clearTimeout(timer);
                    reject(err);
                });
        });
    }

    function setDot(id, state) {
        var el = document.getElementById('dot-' + id);
        if (!el) return;
        el.className = 'dot dot-' + state;
    }

    function setCardStatus(id, state, detail) {
        setDot(id, state);
        var badge = document.getElementById('status-' + id);
        if (badge) {
            badge.textContent = detail;
            badge.className = 'badge-tag status-badge status-' + state;
        }
    }

    function checkOne(svc) {
        var timeout = svc.id === 'llm' ? LLM_HEALTH_TIMEOUT_MS : HEALTH_TIMEOUT_MS;
        return fetchWithTimeout(svc.health, timeout)
            .then(function (res) {
                var ok = res.ok;
                var detail = ok ? '정상' : 'HTTP ' + res.status;
                if (svc.id === 'llm' && ok) detail = '정상 · /llm/stats';
                setCardStatus(svc.id, ok ? 'ok' : 'err', detail);
                return ok;
            })
            .catch(function (err) {
                if (svc.id === 'llm' && err.message === 'timeout') {
                    setCardStatus(svc.id, 'warn', '워커 점유');
                    return false;
                }
                var detail = err.message === 'timeout' ? '응답 지연' : '연결 실패';
                setCardStatus(svc.id, 'err', detail);
                return false;
            });
    }

    function checkProxy() {
        return fetchWithTimeout('/healthz', 3000)
            .then(function (res) {
                var ok = res.ok;
                setDot('proxy', ok ? 'ok' : 'err');
                var label = document.getElementById('proxy-status');
                if (label) label.textContent = ok ? 'nginx 정상' : 'HTTP ' + res.status;
                return ok;
            })
            .catch(function () {
                setDot('proxy', 'err');
                var label = document.getElementById('proxy-status');
                if (label) label.textContent = '연결 실패';
                return false;
            });
    }

    function updateSummary(okCount, total) {
        var el = document.getElementById('summary-status');
        var dot = document.getElementById('dot-summary');
        if (!el || !dot) return;
        if (okCount === total) {
            dot.className = 'dot dot-ok';
            el.textContent = '전체 ' + total + '개 서비스 정상';
        } else if (okCount > 0) {
            dot.className = 'dot dot-warn';
            el.textContent = okCount + ' / ' + total + ' 정상 · 일부 점검 필요';
        } else {
            dot.className = 'dot dot-err';
            el.textContent = '서비스 응답 없음';
        }
    }

    function fmtNum(n) {
        return Number(n || 0).toLocaleString('ko-KR');
    }

    function setText(id, text) {
        var el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function renderBarChart(data, barWidth, step) {
        var max = 0;
        var i;
        for (i = 0; i < data.length; i++) {
            if (data[i].count > max) max = data[i].count;
        }
        if (!max) return '<p class="muted">요청 이력 없음.</p>';
        var svg = '<svg viewBox="0 0 480 140" class="bar-chart" role="img">';
        for (i = 0; i < data.length; i++) {
            var b = data[i];
            var h = max ? (b.count / max * 110) : 0;
            var x = i * step;
            var cx = x + barWidth / 2 + 3;
            svg += '<rect x="' + (x + 3) + '" y="' + (120 - h) + '" width="' + barWidth + '" height="' + h + '" rx="2" class="bar-rect"></rect>';
            if (i % 3 === 0 || data.length <= 7) {
                svg += '<text x="' + cx + '" y="135" text-anchor="middle" class="bar-x">' + (b.label || '') + '</text>';
            }
            if (b.count > 0) {
                svg += '<text x="' + cx + '" y="' + (120 - h - 4) + '" text-anchor="middle" class="bar-v">' + b.count + '</text>';
            }
        }
        svg += '</svg>';
        return svg;
    }

    function renderBarChart7d(data) {
        var max = 0;
        var i;
        for (i = 0; i < data.length; i++) {
            if (data[i].count > max) max = data[i].count;
        }
        if (!max) return '<p class="muted">요청 이력 없음.</p>';
        var svg = '<svg viewBox="0 0 480 140" class="bar-chart" role="img">';
        for (i = 0; i < data.length; i++) {
            var b = data[i];
            var h = max ? (b.count / max * 110) : 0;
            var x = i * 65 + 10;
            svg += '<rect x="' + x + '" y="' + (120 - h) + '" width="50" height="' + h + '" rx="3" class="bar-rect"></rect>';
            svg += '<text x="' + (x + 25) + '" y="135" text-anchor="middle" class="bar-x">' + (b.label || '') + '</text>';
            if (b.count > 0) {
                svg += '<text x="' + (x + 25) + '" y="' + (120 - h - 4) + '" text-anchor="middle" class="bar-v">' + b.count + '</text>';
            }
        }
        svg += '</svg>';
        return svg;
    }

    function renderByService(rows) {
        var el = document.getElementById('stats-by-service');
        if (!el) return;
        if (!rows.length) {
            el.innerHTML = '<p class="muted">서비스별 데이터 없음.</p>';
            return;
        }
        var html = '<table class="info-table"><thead><tr><th>서비스</th><th>요청</th><th>토큰</th><th>비용 (KRW)</th></tr></thead><tbody>';
        rows.forEach(function (r) {
            html += '<tr><td>' + (r.service_id || '—') + '</td><td>' + fmtNum(r.requests) +
                '</td><td>' + fmtNum(r.tokens) + '</td><td>₩' + fmtNum(r.cost_krw) + '</td></tr>';
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    }

    function loadLiveStats() {
        fetchWithTimeout('/llm/api/stats', 8000)
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (data) {
                var gen = document.getElementById('stats-generated');
                if (gen) {
                    gen.textContent = data.generated_at ? '갱신 ' + data.generated_at : '갱신 완료';
                }
                var emptyEl = document.getElementById('stats-empty');
                var contentEl = document.getElementById('stats-content');
                if (data.empty) {
                    if (emptyEl) emptyEl.style.display = '';
                    if (contentEl) contentEl.style.display = 'none';
                    return;
                }
                if (emptyEl) emptyEl.style.display = 'none';
                if (contentEl) contentEl.style.display = '';
                var s = data.summary || {};
                setText('kpi-total', fmtNum(s.total_requests));
                setText('kpi-today', fmtNum(s.today_requests));
                setText('kpi-24h', fmtNum(s.last_24h_requests));
                setText('kpi-success', (s.success_rate != null ? s.success_rate : 0) + '%');
                setText('kpi-latency', fmtNum(s.avg_latency_ms) + ' ms');
                setText('kpi-cost', '₩' + fmtNum(s.cost_krw));
                var h24 = document.getElementById('chart-h24');
                if (h24) h24.innerHTML = renderBarChart(data.h24 || [], 14, 20);
                var h7d = document.getElementById('chart-h7d');
                if (h7d) h7d.innerHTML = renderBarChart7d(data.h7d || []);
                renderByService(data.by_service || []);
                if (gen && s.last_24h_requests === 0 && s.total_requests > 0 && data.recent && data.recent.length) {
                    gen.textContent = (data.generated_at ? '갱신 ' + data.generated_at + ' · ' : '') +
                        '최근 24h 요청 없음 (마지막 ' + data.recent[0].ts + ')';
                }
            })
            .catch(function () {
                var gen = document.getElementById('stats-generated');
                if (gen) gen.textContent = '집계 불러오기 실패';
            });
    }

    function refresh() {
        setDot('summary', 'pending');
        document.getElementById('summary-status').textContent = '확인 중…';
        setDot('proxy', 'pending');
        document.getElementById('proxy-status').textContent = '확인 중…';

        SERVICES.forEach(function (svc) {
            setDot(svc.id, 'pending');
            var badge = document.getElementById('status-' + svc.id);
            if (badge) badge.textContent = '확인 중…';
        });

        var checks = [checkProxy()].concat(SERVICES.map(checkOne));
        Promise.all(checks).then(function (results) {
            var serviceResults = results.slice(1);
            var ok = serviceResults.filter(Boolean).length;
            updateSummary(ok, SERVICES.length);
            var ts = document.getElementById('last-checked');
            if (ts) {
                ts.textContent = new Date().toLocaleString('ko-KR', { hour12: false });
            }
        });
        loadLiveStats();
    }

    var btn = document.getElementById('btn-refresh');
    if (btn) btn.addEventListener('click', refresh);
    refresh();
    setInterval(refresh, STATS_REFRESH_MS);
})();
