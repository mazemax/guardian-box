/**
 * GuardianBox Dashboard
 * Modern, dark, calm UI for fall detection monitoring
 * Vanilla JS + SSE for real-time updates
 */

class GuardianBoxDashboard {
    constructor() {
        this.eventSource = null;
        this.currentEventId = null;
        this.currentState = 'MONITORING';
        this.eventLog = [];
        this.eventHistory = [];
        this.telegramMessages = [];
        this.clips = [];
        
        this.init();
    }
    
    init() {
        this.bindElements();
        this.bindEvents();
        this.connectSSE();
        this.fetchInitialData();
        this.loadClips();
        this.startMetricsPolling();
    }
    
    bindElements() {
        // Header elements
        this.stateBadge = document.getElementById('state-badge');
        this.stateText = document.getElementById('state-text');
        this.memoryDisplay = document.getElementById('memory-display');
        this.gpuDisplay = document.getElementById('gpu-display');
        this.egressDisplay = document.getElementById('egress-display');
        this.metricInternet = document.getElementById('metric-internet');
        
        // Footage elements
        this.footageContainer = document.getElementById('footage-container');
        this.footagePlaceholder = document.getElementById('footage-placeholder');
        this.footageStream = document.getElementById('footage-stream');
        this.clipSelector = document.getElementById('clip-selector');
        this.poseInfo = document.getElementById('pose-info');
        
        // Timeline elements
        this.timelineStages = document.getElementById('timeline-stages');
        this.eventLogList = document.getElementById('event-log-list');
        
        // Telegram elements
        this.telegramMode = document.getElementById('telegram-mode');
        this.phoneMessages = document.getElementById('phone-messages');
        
        // History elements
        this.historyList = document.getElementById('history-list');
        
        // Toast container
        this.toastContainer = document.getElementById('toast-container');
    }
    
    bindEvents() {
        // Demo buttons
        document.querySelectorAll('.demo-btn').forEach(btn => {
            btn.addEventListener('click', () => this.runDemo(btn.dataset.scenario));
        });
        
        // Clip selector
        this.clipSelector?.addEventListener('change', (e) => {
            if (e.target.value) {
                this.selectClip(e.target.value);
            }
        });
        
        // Test Telegram button
        document.getElementById('test-telegram-btn')?.addEventListener('click', () => {
            this.sendTestTelegram();
        });
        
        // Clear timeline
        document.getElementById('clear-timeline-btn')?.addEventListener('click', () => {
            this.clearTimeline();
        });
        
        // Export button
        document.getElementById('export-btn')?.addEventListener('click', () => {
            this.exportEvent();
        });
        
        // Modal close
        document.getElementById('modal-close')?.addEventListener('click', () => {
            document.getElementById('event-modal').style.display = 'none';
        });
        
        // Skeleton toggle
        document.getElementById('skeleton-toggle')?.addEventListener('click', () => {
            this.toggleSkeleton();
        });
    }
    
    // ===== SSE Connection =====
    connectSSE() {
        if (this.eventSource) {
            this.eventSource.close();
        }
        
        this.eventSource = new EventSource('/api/v1/stream');
        
        this.eventSource.onopen = () => {
            console.log('SSE connected');
            this.showToast('Connected to GuardianBox', 'success');
        };
        
        this.eventSource.onerror = (e) => {
            console.error('SSE error:', e);
            this.showToast('Connection lost, reconnecting...', 'warning');
            setTimeout(() => this.connectSSE(), 5000);
        };
        
        // Event handlers
        this.eventSource.addEventListener('connected', (e) => {
            console.log('SSE handshake:', JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('state', (e) => {
            this.handleStateUpdate(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('pose', (e) => {
            this.handlePoseUpdate(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('vlm_result', (e) => {
            this.handleVLMResult(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('checkin', (e) => {
            this.handleCheckIn(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('model_run', (e) => {
            this.handleModelRun(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('alert', (e) => {
            this.handleAlert(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('ack', (e) => {
            this.handleAck(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('telegram_preview', (e) => {
            this.handleTelegramPreview(JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('ping', () => {});
    }
    
    // ===== Data Fetching =====
    async fetchInitialData() {
        try {
            // Fetch status
            const statusRes = await fetch('/api/v1/status');
            const status = await statusRes.json();
            this.updateMetrics(status);
            this.updateState(status.state);
            
            // Fetch events
            const eventsRes = await fetch('/api/v1/events?limit=10');
            const events = await eventsRes.json();
            this.eventHistory = events;
            this.renderHistory();
            
            // Pre-populate timeline from latest event
            if (events.length > 0) {
                this.populateTimelineFromEvent(events[0]);
            }
            
            // Check Telegram mode
            const healthRes = await fetch('/api/v1/health');
            const health = await healthRes.json();
            if (health.telegram_connected) {
                this.telegramMode.textContent = 'Live';
                this.telegramMode.classList.add('live');
            }
        } catch (e) {
            console.error('Failed to fetch initial data:', e);
        }
    }
    
    async loadClips() {
        try {
            const res = await fetch('/api/v1/clips');
            if (res.ok) {
                this.clips = await res.json();
                this.populateClipSelector();
            }
        } catch (e) {
            console.log('No clips endpoint available');
        }
    }
    
    populateClipSelector() {
        if (!this.clipSelector) return;
        
        this.clipSelector.innerHTML = '<option value="">Camera / Clip...</option>';
        
        // Default clip labels (used if API doesn't provide names)
        const defaultClips = [
            { path: 'samples/clip1.mp4', name: 'clip1 — Slow collapse (chest pain) → Alert' },
            { path: 'samples/clip2.mp4', name: 'clip2 — Fast fall (walker) → Alert' },
            { path: 'samples/clip3.mp4', name: 'clip3 — Prayer (sujud) → Quiet' },
            { path: 'samples/clip4.mp4', name: 'clip4 — Slow collapse (dizzy) → Alert' },
            { path: 'samples/clip5.mp4', name: 'clip5 — Normal walking → Quiet' },
        ];
        
        const clipsToUse = this.clips.length > 0 ? this.clips : defaultClips;
        
        if (clipsToUse.length > 0) {
            const group = document.createElement('optgroup');
            group.label = 'Sample Clips';
            clipsToUse.forEach(clip => {
                const opt = document.createElement('option');
                opt.value = clip.path;
                opt.textContent = clip.name;
                group.appendChild(opt);
            });
            this.clipSelector.appendChild(group);
        }
    }
    
    startMetricsPolling() {
        setInterval(() => this.fetchMetrics(), 5000);
    }
    
    async fetchMetrics() {
        try {
            const res = await fetch('/api/v1/status');
            const data = await res.json();
            this.updateMetrics(data);
        } catch (e) {
            // Silently fail
        }
    }
    
    // ===== Event Handlers =====
    handleStateUpdate(data) {
        this.currentEventId = data.event_id;
        this.updateState(data.state);
        
        this.addLogEntry('SYSTEM', `State → ${data.state}`, data.reason || '');
        
        // Update stages based on state
        this.updateStageForState(data.state, data);
    }
    
    handlePoseUpdate(data) {
        // Update pose info overlay
        const poseInfo = document.getElementById('pose-info');
        if (poseInfo) {
            poseInfo.querySelector('.pose-zone').textContent = `zone: ${data.zone || '--'}`;
            poseInfo.querySelector('.pose-angle').textContent = `torso: ${data.torso_angle?.toFixed(0) || '--'}°`;
            poseInfo.querySelector('.pose-still').textContent = `still: ${data.stillness_seconds?.toFixed(1) || '--'}s`;
        }
        
        // Update pose stage
        this.setStageStatus('pose', 'done', 
            `Trigger: torso ${data.torso_angle?.toFixed(0)}°, still ${data.stillness_seconds?.toFixed(1)}s`);
    }
    
    handleVLMResult(data) {
        const assessment = data.assessment;
        const decision = data.decision;
        
        if (assessment) {
            this.setStageStatus('vlm', 'done',
                `${assessment.scene_class} (conf: ${assessment.confidence.toFixed(2)})`);
            this.addLogEntry('VLM', `"${assessment.scene_class}"`, assessment.description || '');
        } else {
            this.setStageStatus('vlm', 'skipped', 'VLM unavailable');
            this.addLogEntry('VLM', 'Unavailable', 'Proceeding with check-in');
        }
        
        if (decision) {
            this.setStageStatus('decision', 'done', `${decision.rule}: ${decision.outcome}`);
            this.addLogEntry('SYSTEM', `Decision: ${decision.rule}`, decision.reason);
        }
    }
    
    handleCheckIn(data) {
        if (data.state === 'listening') {
            this.setStageStatus('checkin', 'active', `Playing: ${data.phrase_id} (${data.lang})`);
            this.setStageStatus('stt', 'active', 'Listening...');
            this.addLogEntry('VOICE', `▶ ${data.phrase_id}`, `Attempt ${data.attempt}/3 (${data.lang})`);
        } else if (data.state === 'classified') {
            const transcript = data.transcript ? `"${data.transcript}"` : '(no speech)';
            this.setStageStatus('stt', 'done', `${transcript} → ${data.intent}`);
            this.addLogEntry('STT', transcript, `Intent: ${data.intent}${data.keyword ? ` (keyword: ${data.keyword})` : ''}`);
        }
    }
    
    handleModelRun(data) {
        // Add to log if interesting
        if (data.error) {
            this.addLogEntry(data.role.toUpperCase(), 'Error', data.error);
        }
    }
    
    handleAlert(data) {
        this.setStageStatus('alert', 'done', `Sent to ${data.contact} (${data.status})`);
        this.addLogEntry('ALERT', `Alert sent to ${data.contact}`, `Level: ${data.level}`);
        
        // Create Telegram preview
        this.addTelegramMessage({
            type: 'alert',
            level: data.level,
            contact: data.contact,
            event_id: data.event_id,
            text: data.text || this.generateAlertText(data),
        });
        
        this.showToast(`Alert sent to ${data.contact}`, 'warning');
    }
    
    handleAck(data) {
        this.addLogEntry('SYSTEM', `✔ ${data.contact} acknowledged`, data.action);
        
        // Update Telegram preview with ack
        this.addTelegramAck(data);
        
        this.showToast(`${data.contact} responded: ${data.action}`, 'success');
    }
    
    handleTelegramPreview(data) {
        this.addTelegramMessage(data);
    }
    
    // ===== UI Updates =====
    updateState(state) {
        this.currentState = state;
        
        if (this.stateBadge) {
            this.stateBadge.dataset.state = state;
        }
        if (this.stateText) {
            this.stateText.textContent = state.replace(/_/g, ' ');
        }
        
        // Update resident status
        const statusDot = document.querySelector('.status-dot');
        const statusText = document.querySelector('.resident-status span:last-child');
        if (statusDot && statusText) {
            if (state === 'MONITORING' || state === 'ARMED') {
                statusDot.className = 'status-dot active';
                statusText.textContent = 'Monitoring';
            } else if (state.includes('ALERT')) {
                statusDot.className = 'status-dot';
                statusDot.style.background = 'var(--accent-red)';
                statusText.textContent = 'Alerting';
            } else {
                statusDot.className = 'status-dot';
                statusDot.style.background = 'var(--accent-amber)';
                statusText.textContent = state.replace(/_/g, ' ');
            }
        }
    }
    
    updateMetrics(data) {
        if (this.memoryDisplay && data.memory_used_gb !== undefined) {
            const used = data.memory_used_gb.toFixed(0);
            const total = data.memory_total_gb?.toFixed(0) || 128;
            this.memoryDisplay.textContent = `${used}/${total} GB`;
        }
        
        if (this.gpuDisplay && data.gpu_util_percent !== undefined) {
            this.gpuDisplay.textContent = `${data.gpu_util_percent.toFixed(0)}%`;
        }
        
        if (this.egressDisplay) {
            const bytes = data.egress_bytes_today || 0;
            this.egressDisplay.textContent = this.formatBytes(bytes);
        }
        
        if (this.metricInternet) {
            if (data.internet_reachable === false) {
                this.metricInternet.classList.add('offline');
                this.metricInternet.querySelector('.metric-value').textContent = 'Offline';
            } else {
                this.metricInternet.classList.remove('offline');
                this.metricInternet.querySelector('.metric-value').textContent = 'Online';
            }
        }
        
        // Update video/image bytes
        const videoBytes = document.getElementById('video-bytes-out');
        const imageBytes = document.getElementById('image-bytes-out');
        if (videoBytes) videoBytes.textContent = data.video_bytes_out || 0;
        if (imageBytes) imageBytes.textContent = data.video_bytes_out || 0;
    }
    
    // ===== Timeline Stages =====
    setStageStatus(stageId, status, detail) {
        const stage = document.getElementById(`stage-${stageId}`);
        if (!stage) return;
        
        stage.dataset.status = status;
        
        const detailEl = document.getElementById(`stage-${stageId}-detail`);
        if (detailEl && detail) {
            detailEl.textContent = detail;
        }
        
        const timeEl = document.getElementById(`stage-${stageId}-time`);
        if (timeEl && status !== 'pending') {
            timeEl.textContent = this.formatTime(new Date());
        }
    }
    
    updateStageForState(state, data) {
        switch (state) {
            case 'SUSPECTED':
                this.setStageStatus('pose', 'active', 'Fall suspected...');
                break;
            case 'CONFIRMING':
                this.setStageStatus('pose', 'done', data.reason || 'Trigger detected');
                this.setStageStatus('vlm', 'active', 'Analyzing frames...');
                break;
            case 'CHECKING_IN':
                this.setStageStatus('checkin', 'active', 'Starting check-in...');
                break;
            case 'LISTENING':
                this.setStageStatus('stt', 'active', 'Listening for response...');
                break;
            case 'DECIDING':
                this.setStageStatus('decision', 'active', 'Evaluating response...');
                break;
            case 'ALERTING':
                this.setStageStatus('alert', 'active', 'Sending alert...');
                break;
            case 'AWAITING_ACK':
                this.setStageStatus('alert', 'done', 'Alert sent, waiting for response');
                break;
            case 'RESOLVED':
            case 'STOOD_DOWN':
                this.resetStages();
                this.addToHistory(data);
                break;
            case 'MONITORING':
                this.resetStages();
                break;
        }
    }
    
    resetStages() {
        ['pose', 'vlm', 'checkin', 'stt', 'decision', 'alert'].forEach(id => {
            this.setStageStatus(id, 'pending', 'Waiting...');
            const timeEl = document.getElementById(`stage-${id}-time`);
            if (timeEl) timeEl.textContent = '--:--:--';
        });
    }
    
    populateTimelineFromEvent(event) {
        if (!event) return;
        
        // Reset first
        this.resetStages();
        this.eventLog = [];
        
        // Populate from event data
        if (event.trigger) {
            this.setStageStatus('pose', 'done', `Trigger: ${event.trigger}`);
        }
        
        if (event.vlm_assessment) {
            this.setStageStatus('vlm', 'done', 
                `${event.vlm_assessment.scene_class} (${event.vlm_assessment.confidence.toFixed(2)})`);
        }
        
        if (event.check_in_attempts?.length > 0) {
            const last = event.check_in_attempts[event.check_in_attempts.length - 1];
            this.setStageStatus('checkin', 'done', `${last.phrase_id} played`);
            this.setStageStatus('stt', 'done', 
                last.transcript ? `"${last.transcript}" → ${last.intent}` : `(silence) → ${last.intent}`);
        }
        
        if (event.decision_row) {
            this.setStageStatus('decision', 'done', event.decision_row);
        }
        
        if (event.alerts?.length > 0) {
            this.setStageStatus('alert', 'done', `Sent (${event.alerts.length} alerts)`);
        }
        
        this.renderEventLog();
    }
    
    // ===== Event Log =====
    addLogEntry(role, message, detail) {
        const entry = {
            time: this.formatTime(new Date()),
            role,
            message,
            detail
        };
        
        this.eventLog.unshift(entry);
        if (this.eventLog.length > 50) {
            this.eventLog.pop();
        }
        
        this.renderEventLog();
    }
    
    renderEventLog() {
        if (!this.eventLogList) return;
        
        if (this.eventLog.length === 0) {
            this.eventLogList.innerHTML = '<div class="phone-empty"><p>No events yet</p></div>';
            return;
        }
        
        this.eventLogList.innerHTML = this.eventLog.map(entry => `
            <div class="log-item">
                <span class="log-time">${entry.time}</span>
                <span class="log-role ${entry.role.toLowerCase()}">${entry.role}</span>
                <span class="log-message">${entry.message}${entry.detail ? ` <span style="color:var(--text-muted)">${entry.detail}</span>` : ''}</span>
            </div>
        `).join('');
    }
    
    clearTimeline() {
        this.resetStages();
        this.eventLog = [];
        this.renderEventLog();
        this.telegramMessages = [];
        this.renderTelegramMessages();
    }
    
    // ===== Telegram Preview =====
    addTelegramMessage(data) {
        this.telegramMessages.push({
            ...data,
            time: new Date()
        });
        this.renderTelegramMessages();
    }
    
    addTelegramAck(data) {
        // Find the related message and add ack info
        const msg = this.telegramMessages.find(m => m.event_id === data.event_id);
        if (msg) {
            msg.ack = data;
        }
        this.renderTelegramMessages();
    }
    
    renderTelegramMessages() {
        if (!this.phoneMessages) return;
        
        if (this.telegramMessages.length === 0) {
            this.phoneMessages.innerHTML = `
                <div class="phone-empty">
                    <span>📱</span>
                    <p>Telegram messages will appear here</p>
                    <p class="hint">Run a demo to see the alert flow</p>
                </div>`;
            return;
        }
        
        this.phoneMessages.innerHTML = this.telegramMessages.map(msg => {
            if (msg.type === 'alert') {
                return this.renderTelegramAlert(msg);
            }
            return '';
        }).join('');
        
        // Scroll to bottom
        this.phoneMessages.scrollTop = this.phoneMessages.scrollHeight;
    }
    
    renderTelegramAlert(msg) {
        const levelEmoji = msg.level === 'urgent' ? '🚨' : '⚠️';
        const levelClass = msg.level === 'urgent' ? 'urgent' : '';
        
        const text = msg.text || this.generateAlertText(msg);
        
        let ackHtml = '';
        if (msg.ack) {
            ackHtml = `<div style="margin-top:8px;color:var(--accent-green);font-size:11px">✔ ${msg.ack.contact}: ${msg.ack.action}</div>`;
        }
        
        return `
            <div class="tg-message">
                <div class="tg-bubble">
                    <div class="tg-level ${levelClass}">${levelEmoji} ${msg.level?.toUpperCase() || 'ALERT'}</div>
                    <div class="tg-body">${text}</div>
                    <div class="tg-footer">No photos or video are ever sent.</div>
                    ${ackHtml}
                </div>
                <div class="tg-buttons">
                    <button class="tg-btn" onclick="dashboard.sendAck('${msg.event_id}', 'calling')">📞 Calling</button>
                    <button class="tg-btn" onclick="dashboard.sendAck('${msg.event_id}', 'on_my_way')">🏃 On my way</button>
                    <button class="tg-btn" onclick="dashboard.sendAck('${msg.event_id}', 'false_alarm')">✅ False alarm</button>
                    <button class="tg-btn" onclick="dashboard.sendAck('${msg.event_id}', 'call_999_info')">🆘 999 info</button>
                </div>
                <div class="tg-time">${this.formatTime(msg.time)}</div>
            </div>
        `;
    }
    
    generateAlertText(data) {
        return `${data.contact || 'Ahmad'} · Living room · ${this.formatTime(new Date())} (MYT)

What GuardianBox saw: Person on the floor, not moving.
What it tried: Asked 3 times. No reply.

Please call now. If it's an emergency, call 999.`;
    }
    
    async sendAck(eventId, action) {
        try {
            const res = await fetch('/api/v1/demo/ack', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ event_id: eventId, action })
            });
            
            if (res.ok) {
                this.showToast(`Acknowledged: ${action}`, 'success');
            }
        } catch (e) {
            console.error('Failed to send ack:', e);
            this.showToast('Failed to send acknowledgement', 'error');
        }
    }
    
    // ===== History =====
    addToHistory(data) {
        const event = {
            id: data.event_id || this.currentEventId,
            state: data.state,
            time: new Date(),
            reason: data.reason
        };
        
        this.eventHistory.unshift(event);
        if (this.eventHistory.length > 20) {
            this.eventHistory.pop();
        }
        
        this.renderHistory();
    }
    
    renderHistory() {
        if (!this.historyList) return;
        
        if (this.eventHistory.length === 0) {
            this.historyList.innerHTML = '<div class="history-empty">No events yet</div>';
            return;
        }
        
        this.historyList.innerHTML = this.eventHistory.slice(0, 10).map(event => {
            const icon = event.state?.includes('ALERT') ? '🚨' : 
                        event.state === 'RESOLVED' ? '✅' : 
                        event.state === 'STOOD_DOWN' ? '🕌' : '📋';
            
            const badge = event.state?.includes('ALERT') ? 'alert' : 'resolved';
            
            const timeStr = event.started_at ? 
                new Date(event.started_at).toLocaleTimeString() : 
                (event.time ? this.formatTime(event.time) : '--:--');
            
            return `
                <div class="history-item" onclick="dashboard.showEventDetail('${event.id}')">
                    <div class="history-icon">${icon}</div>
                    <div class="history-info">
                        <div class="history-title">${event.trigger || event.state || 'Event'}</div>
                        <div class="history-time">${timeStr}</div>
                    </div>
                    <span class="history-badge ${badge}">${event.decision_row || event.state || ''}</span>
                </div>
            `;
        }).join('');
    }
    
    async showEventDetail(eventId) {
        try {
            const res = await fetch(`/api/v1/events/${eventId}`);
            if (res.ok) {
                const event = await res.json();
                document.getElementById('modal-body').innerHTML = `<pre>${JSON.stringify(event, null, 2)}</pre>`;
                document.getElementById('event-modal').style.display = 'flex';
            }
        } catch (e) {
            console.error('Failed to load event detail:', e);
        }
    }
    
    // ===== Demo Actions =====
    async runDemo(scenario) {
        this.clearTimeline();
        this.showToast(`Running ${scenario} demo...`, 'info');
        
        const scenarios = {
            fall: {
                trigger_type: 'fast_fall',
                torso_angle: 78,
                stillness_seconds: 5,
                rapid_descent: true,
                zone: 'floor'
            },
            slow_collapse: {
                trigger_type: 'slow_low_posture',
                torso_angle: 65,
                stillness_seconds: 20,
                rapid_descent: false,
                zone: 'floor'
            },
            prayer: {
                trigger_type: 'slow_low_posture',
                torso_angle: 85,
                stillness_seconds: 8,
                prayer_cycles: 4,
                rapid_descent: false,
                zone: 'prayer_area'
            },
            ok_reply: {
                trigger_type: 'fast_fall',
                torso_angle: 70,
                stillness_seconds: 4,
                rapid_descent: true,
                zone: 'floor',
                _simulate_reply: 'Saya okey'
            },
            silence: {
                trigger_type: 'fast_fall',
                torso_angle: 80,
                stillness_seconds: 5,
                rapid_descent: true,
                zone: 'floor'
            }
        };
        
        const data = scenarios[scenario] || scenarios.fall;
        const simulateReply = data._simulate_reply;
        delete data._simulate_reply;
        
        try {
            const res = await fetch('/api/v1/demo/pose-trigger', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            
            if (res.ok) {
                const result = await res.json();
                console.log('Demo triggered:', result);
                
                // If this scenario has a simulated reply, inject it after a delay
                if (simulateReply) {
                    setTimeout(() => this.simulateReply(simulateReply), 2000);
                }
            }
        } catch (e) {
            console.error('Failed to run demo:', e);
            this.showToast('Failed to run demo', 'error');
        }
    }
    
    async simulateReply(transcript) {
        try {
            const res = await fetch('/api/v1/demo/simulate-reply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ transcript, lang: 'ms' })
            });
            
            if (res.ok) {
                console.log('Simulated reply:', transcript);
            }
        } catch (e) {
            console.log('Failed to simulate reply:', e);
        }
    }
    
    async selectClip(clipPath) {
        try {
            const res = await fetch('/api/v1/vision/source', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: clipPath })
            });
            
            if (res.ok) {
                this.showToast(`Switched to ${clipPath}`, 'success');
                this.startVideoStream();
            }
        } catch (e) {
            console.error('Failed to select clip:', e);
        }
    }
    
    startVideoStream() {
        if (this.footageStream && this.footagePlaceholder) {
            this.footageStream.src = '/api/v1/vision/stream.mjpg?' + Date.now();
            this.footageStream.style.display = 'block';
            this.footagePlaceholder.style.display = 'none';
            
            this.footageStream.onerror = () => {
                this.footageStream.style.display = 'none';
                this.footagePlaceholder.style.display = 'flex';
            };
        }
    }
    
    toggleSkeleton() {
        // Toggle between skeleton overlay and regular view
        this.showToast('Skeleton overlay toggled', 'info');
    }
    
    async sendTestTelegram() {
        try {
            const res = await fetch('/api/v1/telegram/test', {
                method: 'POST'
            });
            
            if (res.ok) {
                this.showToast('Test message sent!', 'success');
            } else {
                const data = await res.json();
                this.showToast(data.detail || 'Failed to send test message', 'error');
            }
        } catch (e) {
            console.error('Failed to send test:', e);
            this.showToast('Failed to send test message', 'error');
        }
    }
    
    async exportEvent() {
        if (!this.currentEventId) {
            this.showToast('No active event to export', 'warning');
            return;
        }
        
        window.open(`/api/v1/events/${this.currentEventId}/export`, '_blank');
    }
    
    // ===== Utilities =====
    formatTime(date) {
        return date.toLocaleTimeString('en-GB', { 
            hour: '2-digit', 
            minute: '2-digit', 
            second: '2-digit' 
        });
    }
    
    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
    
    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `<span>${message}</span>`;
        
        this.toastContainer.appendChild(toast);
        
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new GuardianBoxDashboard();
});
