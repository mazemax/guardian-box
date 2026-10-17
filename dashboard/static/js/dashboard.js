/**
 * GuardianBox Dashboard
 * 
 * Vanilla JS + SSE for real-time updates from the orchestrator.
 */

class GuardianBoxDashboard {
    constructor() {
        this.eventSource = null;
        this.currentEventId = null;
        this.presenterMode = false;
        this.timeline = [];
        
        this.init();
    }
    
    init() {
        this.bindElements();
        this.bindEvents();
        this.connectSSE();
        this.fetchInitialStatus();
    }
    
    bindElements() {
        this.stateChip = document.getElementById('state-chip');
        this.eventState = document.getElementById('event-state');
        this.eventDetail = document.getElementById('event-detail');
        this.eventActions = document.getElementById('event-actions');
        this.eventCard = document.getElementById('event-card');
        this.timelineEl = document.getElementById('timeline');
        this.skeletonInfo = document.getElementById('skeleton-info');
        this.skeletonFigure = document.getElementById('skeleton-figure');
        
        this.egressMsgs = document.getElementById('egress-msgs');
        this.egressBytes = document.getElementById('egress-bytes');
        this.videoBytes = document.getElementById('video-bytes');
        this.imageBytes = document.getElementById('image-bytes');
        
        this.memoryUsed = document.getElementById('memory-used');
        this.memoryVal = document.getElementById('memory-val');
        this.memoryBar = document.getElementById('memory-bar');
        this.gpuUtil = document.getElementById('gpu-util');
        this.gpuVal = document.getElementById('gpu-val');
        this.gpuBar = document.getElementById('gpu-bar');
        
        this.internetChip = document.getElementById('internet-chip');
        this.presenterBtn = document.getElementById('presenter-btn');
        this.demoControls = document.getElementById('demo-controls');
        this.exportBtn = document.getElementById('export-btn');
    }
    
    bindEvents() {
        // Presenter mode toggle
        this.presenterBtn.addEventListener('click', () => this.togglePresenterMode());
        
        // Demo scenario buttons
        document.querySelectorAll('.demo-btn[data-scenario]').forEach(btn => {
            btn.addEventListener('click', () => this.triggerScenario(btn.dataset.scenario));
        });
        
        // Acknowledge button
        document.getElementById('ack-btn')?.addEventListener('click', () => this.simulateAck());
        
        // Offline simulation
        document.getElementById('offline-btn')?.addEventListener('click', () => this.toggleOffline());
        
        // Profile selection
        document.querySelectorAll('input[name="profile"]').forEach(radio => {
            radio.addEventListener('change', () => this.setProfile(radio.value));
        });
        
        // Export button
        this.exportBtn.addEventListener('click', () => this.exportEvent());
        
        // Simulate reply
        document.getElementById('simulate-reply')?.addEventListener('change', (e) => {
            if (e.target.value) {
                this.setSTTResponse(e.target.value);
                e.target.value = '';
            }
        });
    }
    
    connectSSE() {
        if (this.eventSource) {
            this.eventSource.close();
        }
        
        this.eventSource = new EventSource('/api/v1/stream');
        
        this.eventSource.onopen = () => {
            console.log('SSE connected');
        };
        
        this.eventSource.onerror = (e) => {
            console.error('SSE error:', e);
            setTimeout(() => this.connectSSE(), 5000);
        };
        
        this.eventSource.addEventListener('connected', (e) => {
            console.log('SSE connected:', JSON.parse(e.data));
        });
        
        this.eventSource.addEventListener('state', (e) => {
            const data = JSON.parse(e.data);
            this.handleStateUpdate(data);
        });
        
        this.eventSource.addEventListener('pose', (e) => {
            const data = JSON.parse(e.data);
            this.handlePoseUpdate(data);
        });
        
        this.eventSource.addEventListener('vlm_result', (e) => {
            const data = JSON.parse(e.data);
            this.handleVLMResult(data);
        });
        
        this.eventSource.addEventListener('checkin', (e) => {
            const data = JSON.parse(e.data);
            this.handleCheckIn(data);
        });
        
        this.eventSource.addEventListener('model_run', (e) => {
            const data = JSON.parse(e.data);
            this.handleModelRun(data);
        });
        
        this.eventSource.addEventListener('alert', (e) => {
            const data = JSON.parse(e.data);
            this.handleAlert(data);
        });
        
        this.eventSource.addEventListener('ack', (e) => {
            const data = JSON.parse(e.data);
            this.handleAck(data);
        });
        
        this.eventSource.addEventListener('ping', () => {
            // Keep-alive ping
        });
    }
    
    async fetchInitialStatus() {
        try {
            const response = await fetch('/api/v1/status');
            const data = await response.json();
            this.updateStatus(data);
        } catch (e) {
            console.error('Failed to fetch status:', e);
        }
    }
    
    updateStatus(data) {
        this.updateStateChip(data.state);
        
        if (this.memoryUsed) {
            this.memoryUsed.textContent = data.memory_used_gb.toFixed(0);
            this.memoryVal.textContent = data.memory_used_gb.toFixed(0);
            const memPct = (data.memory_used_gb / data.memory_total_gb * 100).toFixed(0);
            this.memoryBar.style.width = `${memPct}%`;
        }
        
        if (this.gpuUtil) {
            this.gpuUtil.textContent = data.gpu_util_percent.toFixed(0);
            this.gpuVal.textContent = data.gpu_util_percent.toFixed(0);
            this.gpuBar.style.width = `${data.gpu_util_percent}%`;
        }
        
        if (this.egressMsgs) {
            this.egressMsgs.textContent = data.egress_messages_today;
            this.egressBytes.textContent = this.formatBytes(data.egress_bytes_today);
            this.videoBytes.textContent = data.video_bytes_out;
            this.imageBytes.textContent = data.video_bytes_out;
        }
        
        if (data.internet_reachable) {
            this.internetChip.textContent = '🌐 Internet OK';
            this.internetChip.classList.remove('offline');
        } else {
            this.internetChip.textContent = '📴 Internet DOWN';
            this.internetChip.classList.add('offline');
        }
    }
    
    updateStateChip(state) {
        this.stateChip.textContent = state;
        this.stateChip.className = 'state-chip ' + state.toLowerCase().replace(/_/g, '-');
        
        if (this.eventState) {
            this.eventState.textContent = state;
            this.eventState.className = 'event-state';
            if (state.includes('ALERT')) {
                this.eventState.classList.add('alert');
            }
        }
    }
    
    handleStateUpdate(data) {
        this.updateStateChip(data.state);
        this.currentEventId = data.event_id;
        
        let detail = '';
        switch (data.state) {
            case 'MONITORING':
                detail = 'All quiet. GuardianBox is watching the living room.';
                this.eventActions.style.display = 'none';
                break;
            case 'SUSPECTED':
                detail = `Potential fall detected. Analyzing frames...`;
                break;
            case 'CONFIRMING':
                detail = 'Sending frames to Nemotron vision for confirmation...';
                break;
            case 'CHECKING_IN':
                detail = 'Asking if the resident is okay...';
                this.eventActions.style.display = 'block';
                break;
            case 'LISTENING':
                detail = 'Listening for response...';
                break;
            case 'ALERTING':
                detail = 'Sending alert to family...';
                break;
            case 'AWAITING_ACK':
                detail = 'Alert sent. Waiting for family response...';
                break;
            case 'RESOLVED':
                detail = `Event resolved. ${data.reason || ''}`;
                this.eventActions.style.display = 'none';
                break;
            case 'STOOD_DOWN':
                detail = data.reason || 'Activity recognized as normal. Standing down.';
                break;
        }
        
        if (this.eventDetail) {
            this.eventDetail.textContent = detail;
        }
        
        this.addTimelineItem({
            role: 'SYSTEM',
            content: `State → ${data.state}`,
            detail: data.reason || ''
        });
    }
    
    handlePoseUpdate(data) {
        if (this.skeletonInfo) {
            this.skeletonInfo.textContent = 
                `zone: ${data.zone || 'floor'} · torso ${data.torso_angle?.toFixed(0) || 0}° · still ${data.stillness_seconds?.toFixed(1) || 0} s`;
        }
        
        // Update skeleton figure based on torso angle
        if (this.skeletonFigure && data.torso_angle) {
            const angle = data.torso_angle;
            this.skeletonFigure.setAttribute('transform', 
                `translate(200, 150) rotate(${Math.min(angle, 90)})`);
        }
    }
    
    handleVLMResult(data) {
        const assessment = data.assessment;
        const decision = data.decision;
        
        let content = '';
        if (assessment) {
            content = `"${assessment.scene_class}" (conf: ${assessment.confidence.toFixed(2)})`;
        } else {
            content = 'VLM unavailable - proceeding with check-in';
        }
        
        this.addTimelineItem({
            role: 'VISION',
            model: 'Nemotron Omni',
            content: content,
            detail: assessment?.description || '',
            json: assessment
        });
        
        if (decision) {
            this.addTimelineItem({
                role: 'AGENT',
                model: 'Nemotron Omni',
                content: `${decision.rule}: ${decision.outcome}`,
                detail: decision.reason
            });
        }
    }
    
    handleCheckIn(data) {
        if (data.state === 'listening') {
            this.addTimelineItem({
                role: 'VOICE',
                content: `▶ ${data.phrase_id} (${data.lang})`,
                detail: `Attempt ${data.attempt}/3`
            });
            
            this.addTimelineItem({
                role: 'STT',
                model: 'Malaysian Whisper',
                content: `Listening...`,
                detail: ''
            });
        } else if (data.state === 'classified') {
            // Update the last STT item
            const lastSTT = this.timeline.find(t => t.role === 'STT' && t.content === 'Listening...');
            if (lastSTT) {
                lastSTT.content = data.transcript ? `"${data.transcript}"` : '(no speech)';
                lastSTT.detail = data.intent ? `Intent: ${data.intent}` : '';
                if (data.keyword) {
                    lastSTT.detail += ` (keyword: ${data.keyword})`;
                }
            }
            this.renderTimeline();
        }
    }
    
    handleModelRun(data) {
        // Model runs are shown in context, not separately
    }
    
    handleAlert(data) {
        this.addTimelineItem({
            role: 'ALERT',
            content: `Alert sent to ${data.contact}`,
            detail: `Level: ${data.level}, Status: ${data.status}`
        });
        
        // Update egress counter
        this.fetchEgress();
    }
    
    handleAck(data) {
        this.addTimelineItem({
            role: 'SYSTEM',
            content: `✔ ${data.contact} acknowledged: "${data.action}"`,
            detail: ''
        });
    }
    
    addTimelineItem(item) {
        const now = new Date();
        const time = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        this.timeline.unshift({
            time,
            ...item
        });
        
        // Keep only last 50 items
        if (this.timeline.length > 50) {
            this.timeline = this.timeline.slice(0, 50);
        }
        
        this.renderTimeline();
    }
    
    renderTimeline() {
        if (!this.timelineEl) return;
        
        if (this.timeline.length === 0) {
            this.timelineEl.innerHTML = '<div class="timeline-empty">Events will appear here when activity is detected.</div>';
            return;
        }
        
        this.timelineEl.innerHTML = this.timeline.map(item => `
            <div class="timeline-item">
                <span class="timeline-time">${item.time}</span>
                <span class="timeline-role ${item.role.toLowerCase()}">${item.role}</span>
                <div class="timeline-content">
                    ${item.model ? `<span class="timeline-model">${item.model}</span>` : ''}
                    <div class="timeline-output">${item.content}</div>
                    ${item.detail ? `<div class="timeline-output" style="color: var(--text-secondary)">${item.detail}</div>` : ''}
                </div>
                ${item.latency ? `<span class="timeline-latency">${item.latency}ms</span>` : ''}
            </div>
        `).join('');
    }
    
    async fetchEgress() {
        try {
            const response = await fetch('/api/v1/egress');
            const data = await response.json();
            
            if (this.egressMsgs) {
                this.egressMsgs.textContent = data.messages_today;
                this.egressBytes.textContent = this.formatBytes(data.bytes_today);
                this.videoBytes.textContent = data.video_bytes_out;
                this.imageBytes.textContent = data.image_bytes_out;
            }
        } catch (e) {
            console.error('Failed to fetch egress:', e);
        }
    }
    
    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
    
    togglePresenterMode() {
        this.presenterMode = !this.presenterMode;
        document.body.classList.toggle('presenter-mode', this.presenterMode);
        this.presenterBtn.classList.toggle('active', this.presenterMode);
        this.demoControls.style.display = this.presenterMode ? 'block' : 'none';
    }
    
    async triggerScenario(scenario) {
        try {
            const scenarios = {
                fall: {
                    trigger_type: 'fast_fall',
                    torso_angle: 78,
                    stillness_seconds: 5,
                    rapid_descent: true,
                    zone: 'floor'
                },
                prayer: {
                    trigger_type: 'slow_low_posture',
                    torso_angle: 85,
                    stillness_seconds: 8,
                    prayer_cycles: 3,
                    rapid_descent: false,
                    zone: 'prayer_area'
                },
                sitting: {
                    trigger_type: 'slow_low_posture',
                    torso_angle: 45,
                    stillness_seconds: 15,
                    rapid_descent: false,
                    zone: 'floor'
                }
            };
            
            const data = scenarios[scenario] || scenarios.fall;
            
            const response = await fetch('/api/v1/demo/pose-trigger', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            
            const result = await response.json();
            console.log('Triggered scenario:', result);
            
        } catch (e) {
            console.error('Failed to trigger scenario:', e);
        }
    }
    
    async simulateAck() {
        if (!this.currentEventId) {
            console.warn('No current event to acknowledge');
            return;
        }
        
        try {
            const response = await fetch('/api/v1/demo/ack', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    event_id: this.currentEventId,
                    action: 'calling'
                })
            });
            
            const result = await response.json();
            console.log('Acknowledged:', result);
            
        } catch (e) {
            console.error('Failed to acknowledge:', e);
        }
    }
    
    async toggleOffline() {
        try {
            const response = await fetch('/api/v1/network/simulate-offline', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ on: true })
            });
            
            const result = await response.json();
            console.log('Offline simulation:', result);
            
        } catch (e) {
            console.error('Failed to toggle offline:', e);
        }
    }
    
    async setProfile(profile) {
        try {
            const response = await fetch('/api/v1/demo/profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile })
            });
            
            const result = await response.json();
            console.log('Profile set:', result);
            
        } catch (e) {
            console.error('Failed to set profile:', e);
        }
    }
    
    async setSTTResponse(mode) {
        try {
            const response = await fetch(`http://localhost:8001/response-mode/${mode}`, {
                method: 'POST'
            });
            console.log('STT mode set:', mode);
        } catch (e) {
            // STT service might not be accessible directly
            console.log('STT mode change attempted:', mode);
        }
    }
    
    async exportEvent() {
        if (!this.currentEventId) {
            console.warn('No current event to export');
            return;
        }
        
        try {
            window.open(`/api/v1/events/${this.currentEventId}/export`, '_blank');
        } catch (e) {
            console.error('Failed to export:', e);
        }
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new GuardianBoxDashboard();
});
