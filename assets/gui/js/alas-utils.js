/**
 * Alas WebUI Utilities
 * 公告弹窗、截图查看器、自动刷新等前端功能
 * 从 app.py run_js() 运行时注入迁移为静态加载
 */

// ============================================================
// 自动刷新（断连后自动 reload）
// [For develop] Disable by run `reload=0` in console
// ============================================================
(function () {
    window.reload = 1;
    if (window.WebIO && WebIO._state && WebIO._state.CurrentSession) {
        WebIO._state.CurrentSession.on_session_close(function () {
            setTimeout(function () {
                if (window.reload == 1) {
                    location.reload();
                }
            }, 4000);
        });
    }
})();

// ============================================================
// 截图查看器（点击截图放大、缩放、拖拽）
// ============================================================
(function () {
    function sanitizeUrl(url) {
        if (!url) return '';
        var protocol = url.split(':')[0].toLowerCase().trim();
        if (['javascript', 'data', 'vbscript'].indexOf(protocol) !== -1) {
            // Only allow data:image/ for base64 images
            if (url.startsWith('data:image/')) return url;
            return '';
        }
        return url;
    }

    function ensureScreenshotModal() {
        if (document.getElementById('screenshot-modal')) return;
        var modal = document.createElement('div');
        modal.id = 'screenshot-modal';
        Object.assign(modal.style, {
            position: 'fixed',
            left: 0,
            top: 0,
            width: '100vw',
            height: '100vh',
            display: 'none',
            justifyContent: 'center',
            alignItems: 'center',
            background: 'rgba(0,0,0,0.65)',
            zIndex: 99999,
            overflow: 'hidden',
            padding: '20px',
            boxSizing: 'border-box',
            cursor: 'grab'
        });
        var modalImg = document.createElement('img');
        modalImg.id = 'screenshot-modal-img';
        Object.assign(modalImg.style, {
            maxWidth: '100%',
            maxHeight: '90vh',
            objectFit: 'contain',
            boxShadow: '0 4px 20px rgba(0,0,0,0.5)',
            transition: 'transform 0.05s linear',
            transformOrigin: 'center center',
            willChange: 'transform'
        });
        modal.appendChild(modalImg);

        modal.dataset.scale = 1;
        modal.dataset.tx = 0;
        modal.dataset.ty = 0;
        modal.dataset.panning = 0;

        function applyTransform() {
            var s = parseFloat(modal.dataset.scale) || 1;
            var tx = parseFloat(modal.dataset.tx) || 0;
            var ty = parseFloat(modal.dataset.ty) || 0;
            modalImg.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + s + ')';
        }

        modal.addEventListener('wheel', function (e) {
            if (e.ctrlKey) return;
            e.preventDefault();
            var rect = modalImg.getBoundingClientRect();
            var cx = e.clientX - (rect.left + rect.width / 2);
            var cy = e.clientY - (rect.top + rect.height / 2);
            var scale = parseFloat(modal.dataset.scale) || 1;
            var delta = -e.deltaY;
            var factor = delta > 0 ? 1.12 : 0.88;
            var newScale = Math.min(6, Math.max(0.3, scale * factor));

            var tx = parseFloat(modal.dataset.tx) || 0;
            var ty = parseFloat(modal.dataset.ty) || 0;
            modal.dataset.tx = tx - cx * (newScale - scale);
            modal.dataset.ty = ty - cy * (newScale - scale);
            modal.dataset.scale = newScale;
            applyTransform();
        }, { passive: false });

        var start = { x: 0, y: 0 };
        modalImg.addEventListener('mousedown', function (e) {
            e.preventDefault();
            modal.dataset.panning = 1;
            start.x = e.clientX;
            start.y = e.clientY;
            modal.style.cursor = 'grabbing';
        });
        window.addEventListener('mousemove', function (e) {
            if (modal.dataset.panning !== '1') return;
            var dx = e.clientX - start.x;
            var dy = e.clientY - start.y;
            start.x = e.clientX;
            start.y = e.clientY;
            modal.dataset.tx = (parseFloat(modal.dataset.tx) || 0) + dx;
            modal.dataset.ty = (parseFloat(modal.dataset.ty) || 0) + dy;
            applyTransform();
        });
        window.addEventListener('mouseup', function (e) {
            if (modal.dataset.panning === '1') {
                modal.dataset.panning = 0;
                modal.style.cursor = 'grab';
            }
        });

        modalImg.addEventListener('dblclick', function (e) {
            modal.dataset.scale = 1;
            modal.dataset.tx = 0;
            modal.dataset.ty = 0;
            applyTransform();
        });

        modal.addEventListener('click', function (e) {
            if (e.target === modal) modal.style.display = 'none';
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                var m = document.getElementById('screenshot-modal');
                if (m) m.style.display = 'none';
            }
        });

        document.body.appendChild(modal);
    }

    // Ensure modal exists and wire click handler to #screenshot-img
    ensureScreenshotModal();
    function bindScreenshotImg() {
        var img = document.getElementById('screenshot-img');
        if (!img) return;
        img.style.cursor = 'zoom-in';
        img.onclick = function (e) {
            var m = document.getElementById('screenshot-modal');
            var mi = document.getElementById('screenshot-modal-img');
            if (!m || !mi) return;
            var src = img.getAttribute('data-modal-src') || img.src;
            mi.src = sanitizeUrl(src);
            m.dataset.scale = 1;
            m.dataset.tx = 0;
            m.dataset.ty = 0;
            mi.style.transform = '';
            m.style.display = 'flex';
        };
    }
    // Try binding now and also when DOM changes
    bindScreenshotImg();
    var obs = new MutationObserver(function () { bindScreenshotImg(); });
    obs.observe(document.body, { childList: true, subtree: true });
})();

// ============================================================
// 实时截图预览（H264 over WebSocket）
// ============================================================
(function () {
    var state = {
        socket: null,
        controlSocket: null,
        decoder: null,
        renderCanvas: null,
        renderContext: null,
        mediaSource: null,
        sourceBuffer: null,
        queue: [],
        objectUrl: '',
        instance: 'alas',
        codec: 'h264',
        open: false,
        transportId: 0,
        bitrateScale: 1,
        fps: 60,
        width: 640,
        lastChunkAt: 0,
        firstChunkAt: 0,
        lastReconnectAt: 0,
        qualityTimer: null,
        videoPressure: false,
        healthyTicks: 0,
        degradeTicks: 0,
        reconnectingForQuality: false,
        maxrate: '',
        mode: 'auto',
        videoWidth: 1280,
        videoHeight: 720,
        fullscreenControl: false,
        pointerDown: null,
        pointerMoves: 0,
        controlReady: false,
        controlQueue: [],
        keyboardComposing: false,
        rawPending: null
    };

    var BITRATE_STEPS = [0.35, 0.5, 0.7, 1, 1.25];
    var FPS_STEPS = [15, 24, 30, 45, 60, 90, 120, 180, 240];

    function sanitizeText(text) {
        return String(text || '').replace(/[<>&]/g, function (ch) {
            return ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[ch];
        });
    }

    function ensurePanel() {
        var panel = document.getElementById('alas-live-preview');
        if (panel) return panel;

        panel = document.createElement('div');
        panel.id = 'alas-live-preview';
        panel.innerHTML = [
            '<div class="alas-live-preview-head">',
            '<span class="alas-live-preview-title">实时截图</span>',
            '<button class="alas-live-preview-control" type="button" data-live-control="back" title="返回">↩</button>',
            '<button class="alas-live-preview-control" type="button" data-live-control="home" title="主页">⌂</button>',
            '<button class="alas-live-preview-control" type="button" data-live-control="app_switch" title="后台">▣</button>',
            '<button class="alas-live-preview-control" type="button" data-live-control="keyboard" title="手机键盘">⌨</button>',
            '<button class="alas-live-preview-fullscreen" type="button" title="全屏控制">⛶</button>',
            '<button class="alas-live-preview-close" type="button" title="关闭">×</button>',
            '</div>',
            '<video class="alas-live-preview-video" muted autoplay playsinline></video>',
            '<canvas class="alas-live-preview-canvas"></canvas>',
            '<textarea class="alas-live-preview-keyboard-input" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></textarea>',
            '<div class="alas-live-preview-status">连接中</div>'
        ].join('');

        var style = document.createElement('style');
        style.textContent = [
            '#alas-live-preview{position:fixed;right:18px;bottom:18px;width:min(560px,calc(100vw - 36px));background:#101418;border:1px solid rgba(255,255,255,.14);border-radius:8px;box-shadow:0 12px 36px rgba(0,0,0,.35);z-index:99990;overflow:hidden;display:none;}',
            '.alas-live-preview-head{height:38px;display:flex;align-items:center;gap:8px;padding:0 8px 0 12px;background:#1b222b;color:#f2f5f8;font-size:14px;}',
            '.alas-live-preview-title{font-weight:600;margin-right:auto;}',
            '.alas-live-preview-control,.alas-live-preview-fullscreen,.alas-live-preview-close{width:28px;height:28px;border:0;background:transparent;color:#f2f5f8;font-size:20px;line-height:24px;cursor:pointer;}',
            '.alas-live-preview-control:hover,.alas-live-preview-fullscreen:hover,.alas-live-preview-close:hover{background:rgba(255,255,255,.1);}',
            '.alas-live-preview-close{font-size:24px;}',
            '.alas-live-preview-video{display:block;width:100%;aspect-ratio:16/9;background:#000;object-fit:contain;touch-action:none;}',
            '.alas-live-preview-canvas{display:none;width:100%;aspect-ratio:16/9;background:#000;object-fit:contain;touch-action:none;}',
            '.alas-live-preview-keyboard-input{position:absolute;left:0;bottom:0;width:1px;height:1px;opacity:.01;border:0;padding:0;background:transparent;color:transparent;caret-color:transparent;font-size:16px;resize:none;outline:none;}',
            '#alas-live-preview:fullscreen{width:100vw;height:100vh;right:auto;bottom:auto;border:0;border-radius:0;background:#000;}',
            '@media (orientation: portrait){#alas-live-preview:fullscreen{orientation:landscape;}}',
            '#alas-live-preview:fullscreen .alas-live-preview-head{position:absolute;left:0;right:0;top:0;z-index:2;background:rgba(14,19,25,.86);}',
            '#alas-live-preview:fullscreen .alas-live-preview-video{width:100vw;height:100vh;aspect-ratio:auto;}',
            '#alas-live-preview:fullscreen .alas-live-preview-canvas{width:100vw;height:100vh;aspect-ratio:auto;}',
            '.alas-live-preview-status{position:absolute;left:12px;bottom:10px;max-width:calc(100% - 24px);padding:4px 8px;border-radius:4px;background:rgba(0,0,0,.58);color:#fff;font-size:12px;line-height:1.35;pointer-events:none;}'
        ].join('');
        document.head.appendChild(style);
        document.body.appendChild(panel);

        bindSystemButtons(panel);
        bindMobileKeyboard(panel);
        panel.querySelector('.alas-live-preview-close').onclick = function () {
            window.alasStopLivePreview();
        };
        panel.querySelector('.alas-live-preview-fullscreen').onclick = function () {
            enterFullscreenControl();
        };

        return panel;
    }

    function setStatus(text) {
        var panel = ensurePanel();
        var status = panel.querySelector('.alas-live-preview-status');
        status.innerHTML = sanitizeText(text);
        status.style.display = text ? 'block' : 'none';
    }

    function cleanupTransport() {
        state.transportId += 1;
        if (!state.open) {
            stopControl();
            exitFullscreenControl(true);
        }
        if (state.socket) {
            state.socket.onclose = null;
            state.socket.onerror = null;
            state.socket.onmessage = null;
            try { state.socket.close(); } catch (e) { }
            state.socket = null;
        }
        if (state.sourceBuffer) {
            state.sourceBuffer.onupdateend = null;
            state.sourceBuffer = null;
        }
        if (state.decoder) {
            try { state.decoder.close(); } catch (e) { }
            state.decoder = null;
        }
        if (state.mediaSource) {
            try {
                if (state.mediaSource.readyState === 'open') state.mediaSource.endOfStream();
            } catch (e) { }
            state.mediaSource = null;
        }
        if (state.objectUrl) {
            URL.revokeObjectURL(state.objectUrl);
            state.objectUrl = '';
        }
        state.queue = [];
        state.lastChunkAt = 0;
        state.firstChunkAt = 0;
        state.videoPressure = false;
        state.healthyTicks = 0;
        state.degradeTicks = 0;
        state.reconnectingForQuality = false;
        state.maxrate = '';
        state.pointerDown = null;
        state.pointerMoves = 0;
        state.rawPending = null;
        if (state.qualityTimer) {
            clearInterval(state.qualityTimer);
            state.qualityTimer = null;
        }
    }

    function getWebSocketBase(path) {
        var scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
        var pathParts = location.pathname.split('/').filter(Boolean);
        var firstPart = pathParts.length ? pathParts[0] : '';
        var prefix = '';
        if (/^[a-z0-9]{8,}$/.test(firstPart)) prefix = '/' + firstPart;
        return scheme + location.host + prefix + path;
    }

    function appendNext(transportId) {
        if (transportId !== state.transportId) return;
        var sb = state.sourceBuffer;
        if (!sb || sb.updating || !state.queue.length) return;
        try {
            sb.appendBuffer(state.queue.shift());
        } catch (e) {
            if (transportId !== state.transportId) return;
            setStatus(e.message || e);
        }
    }

    function findStartCode(data, from) {
        for (var i = from || 0; i <= data.length - 3; i += 1) {
            if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) return { index: i, size: 3 };
            if (i <= data.length - 4 && data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 1) {
                return { index: i, size: 4 };
            }
        }
        return null;
    }

    function appendBytes(left, right) {
        if (!left || !left.length) return right;
        if (!right || !right.length) return left;
        var merged = new Uint8Array(left.length + right.length);
        merged.set(left, 0);
        merged.set(right, left.length);
        return merged;
    }

    function extractAnnexBNals(buffer, flush) {
        var data = appendBytes(state.rawPending, new Uint8Array(buffer));
        var nals = [];
        var start = findStartCode(data, 0);
        if (!start) {
            state.rawPending = null;
            if (data.length) {
                var nal = new Uint8Array(data.length + 4);
                nal.set([0, 0, 0, 1], 0);
                nal.set(data, 4);
                nals.push(nal);
            }
            return nals;
        }
        if (start.index > 0) data = data.slice(start.index);
        start = findStartCode(data, 0);
        while (start) {
            var next = findStartCode(data, start.index + start.size);
            if (!next) {
                if (flush) {
                    nals.push(data.slice(start.index));
                    state.rawPending = null;
                } else {
                    state.rawPending = data.slice(start.index);
                }
                return nals;
            }
            if (next.index > start.index) nals.push(data.slice(start.index, next.index));
            start = next;
        }
        state.rawPending = null;
        return nals;
    }

    function nalType(nal) {
        var start = findStartCode(nal, 0);
        var offset = start ? start.index + start.size : 0;
        return nal.length > offset ? (nal[offset] & 31) : 0;
    }

    function concatNals(nals) {
        var length = 0;
        for (var i = 0; i < nals.length; i += 1) length += nals[i].length;
        var data = new Uint8Array(length);
        var offset = 0;
        for (var j = 0; j < nals.length; j += 1) {
            data.set(nals[j], offset);
            offset += nals[j].length;
        }
        return data;
    }

    function toArrayBuffer(data) {
        if (data instanceof ArrayBuffer) {
            return Promise.resolve(data);
        }
        if (ArrayBuffer.isView(data)) {
            return Promise.resolve(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
        }
        if (data && typeof data.arrayBuffer === 'function') {
            return data.arrayBuffer();
        }
        return Promise.reject(new TypeError('未知的视频数据类型'));
    }

    function attachRawH264(socket, msg, transportId) {
        var panel = ensurePanel();
        var video = panel.querySelector('.alas-live-preview-video');
        var canvas = panel.querySelector('.alas-live-preview-canvas');
        if (!('VideoDecoder' in window) || !('EncodedVideoChunk' in window)) {
            state.mode = 'screenshot';
            reconnectForQuality('当前浏览器不支持 WebCodecs，回退截图模式');
            return;
        }

        video.style.display = 'none';
        canvas.style.display = 'block';
        canvas.width = msg.width || 640;
        canvas.height = msg.height || 360;
        state.renderCanvas = canvas;
        state.renderContext = canvas.getContext('2d');
        state.socket = socket;
        state.reconnectingForQuality = false;
        startQualityMonitor(transportId);

        var timestamp = 0;
        var frameDuration = Math.max(1, Math.round(1000000 / (msg.fps || state.fps || 60)));
        var configured = false;
        var waitingForKeyFrame = true;
        var accessUnit = [];
        var pendingConfig = {
            codec: msg.codec_string || 'avc1.42E01E',
            optimizeForLatency: true
        };

        state.decoder = new VideoDecoder({
            output: function (frame) {
                if (transportId !== state.transportId) {
                    frame.close();
                    return;
                }
                try {
                    state.renderContext.drawImage(frame, 0, 0, canvas.width, canvas.height);
                    state.videoPressure = false;
                } finally {
                    frame.close();
                }
            },
            error: function (error) {
                setStatus((error && error.message) || 'WebCodecs 解码失败');
                state.videoPressure = true;
            }
        });

        function ensureConfigured() {
            if (configured) return true;
            try {
                state.decoder.configure(pendingConfig);
                configured = true;
                return true;
            } catch (e) {
                setStatus((e && e.message) || 'WebCodecs 初始化失败');
                return false;
            }
        }

        function decodeAccessUnit(force) {
            if (!accessUnit.length) return;
            var hasSlice = false;
            var hasIdr = false;
            for (var i = 0; i < accessUnit.length; i += 1) {
                var type = nalType(accessUnit[i]);
                if (type === 1 || type === 5) hasSlice = true;
                if (type === 5) hasIdr = true;
            }
            if (!hasSlice && !force) return;
            if (waitingForKeyFrame && !hasIdr) {
                accessUnit = [];
                return;
            }
            if (!ensureConfigured()) {
                accessUnit = [];
                return;
            }
            var data = concatNals(accessUnit);
            var chunk = new EncodedVideoChunk({
                type: hasIdr ? 'key' : 'delta',
                timestamp: timestamp,
                duration: frameDuration,
                data: data
            });
            timestamp += frameDuration;
            accessUnit = [];
            waitingForKeyFrame = false;
            try {
                if (state.decoder.decodeQueueSize > 4) {
                    state.videoPressure = true;
                    return;
                }
                state.decoder.decode(chunk);
                setStatus('');
            } catch (e) {
                waitingForKeyFrame = true;
                setStatus((e && e.message) || 'H264 解码失败');
            }
        }

        function handleNals(nals) {
            for (var i = 0; i < nals.length; i += 1) {
                var nal = nals[i];
                var type = nalType(nal);
                if (!type) continue;
                if ((type === 1 || type === 5) && accessUnit.length) {
                    decodeAccessUnit(false);
                }
                accessUnit.push(nal);
            }
            decodeAccessUnit(false);
        }

        socket.onmessage = function (event) {
            if (!state.open || transportId !== state.transportId || state.socket !== socket) return;
            if (typeof event.data === 'string') {
                try {
                    var textMsg = JSON.parse(event.data);
                    if (textMsg.type === 'error') setStatus(textMsg.message);
                    if (textMsg.type === 'resize') {
                        state.videoWidth = textMsg.width || state.videoWidth;
                        state.videoHeight = textMsg.height || state.videoHeight;
                        canvas.width = state.videoWidth;
                        canvas.height = state.videoHeight;
                    }
                } catch (e) { }
                return;
            }
            state.lastChunkAt = Date.now();
            if (!state.firstChunkAt) state.firstChunkAt = state.lastChunkAt;
            toArrayBuffer(event.data).then(function (buffer) {
                handleNals(extractAnnexBNals(buffer, false));
            }).catch(function (e) {
                setStatus((e && e.message) || '视频数据读取失败');
            });
        };
        socket.onerror = function () {
            if (transportId === state.transportId) setStatus('实时截图连接错误');
        };
        socket.onclose = function () {
            if (state.open && transportId === state.transportId) setStatus('实时截图已断开');
        };
    }

    function nearestBitrateStep(scale) {
        var best = BITRATE_STEPS[0];
        var diff = Math.abs(scale - best);
        for (var i = 1; i < BITRATE_STEPS.length; i += 1) {
            var d = Math.abs(scale - BITRATE_STEPS[i]);
            if (d < diff) {
                best = BITRATE_STEPS[i];
                diff = d;
            }
        }
        return best;
    }

    function nearestFpsStep(fps) {
        var best = FPS_STEPS[0];
        var diff = Math.abs(fps - best);
        for (var i = 1; i < FPS_STEPS.length; i += 1) {
            var d = Math.abs(fps - FPS_STEPS[i]);
            if (d < diff) {
                best = FPS_STEPS[i];
                diff = d;
            }
        }
        return best;
    }

    function reconnectForQuality(text) {
        state.lastReconnectAt = Date.now();
        state.healthyTicks = 0;
        state.degradeTicks = 0;
        state.reconnectingForQuality = true;
        setStatus(text);
        setTimeout(function () {
            if (state.open) start(state.instance, state.codec, true);
        }, 80);
    }

    function changeBitrateStep(delta) {
        if (!state.open || state.reconnectingForQuality) return;
        var current = nearestBitrateStep(state.bitrateScale);
        var index = BITRATE_STEPS.indexOf(current);
        var nextIndex = Math.max(0, Math.min(BITRATE_STEPS.length - 1, index + delta));
        var nextScale = BITRATE_STEPS[nextIndex];
        if (nextScale === current) return;
        var now = Date.now();
        var minInterval = delta < 0 ? 4500 : 15000;
        if (now - state.lastReconnectAt < minInterval) return;

        state.bitrateScale = nextScale;
        reconnectForQuality((delta < 0 ? '链路拥塞，降低码率' : '链路稳定，提升码率') + ' ' + Math.round(nextScale * 100) + '%');
    }

    function changeFpsStep(delta) {
        if (!state.open || state.reconnectingForQuality) return false;
        var current = nearestFpsStep(state.fps);
        var index = FPS_STEPS.indexOf(current);
        var nextIndex = Math.max(0, Math.min(FPS_STEPS.length - 1, index + delta));
        var nextFps = FPS_STEPS[nextIndex];
        if (nextFps === current) return false;
        var now = Date.now();
        var minInterval = delta < 0 ? 4500 : 15000;
        if (now - state.lastReconnectAt < minInterval) return false;

        state.fps = nextFps;
        reconnectForQuality((delta < 0 ? '链路拥塞，降低帧率' : '链路稳定，提升帧率') + ' ' + nextFps + ' FPS');
        return true;
    }

    function updateQuality(transportId) {
        if (!state.open || transportId !== state.transportId) return;
        var now = Date.now();
        var noChunkMs = state.lastChunkAt ? now - state.lastChunkAt : 0;
        var startupMs = state.firstChunkAt ? now - state.firstChunkAt : 0;
        var stalled = false;

        if (!state.firstChunkAt && state.lastReconnectAt && now - state.lastReconnectAt > 12000) {
            state.mode = 'screenshot';
            reconnectForQuality('scrcpy 暂无可播放画面，回退截图模式');
            return;
        }
        if (!state.firstChunkAt) return;
        if (startupMs < 8000) return;
        if (state.videoPressure) stalled = true;
        if (state.queue.length > 10) stalled = true;
        if (noChunkMs > Math.max(3500, 1000 * 3 / state.fps)) stalled = true;

        if (stalled) {
            state.degradeTicks += 1;
            state.healthyTicks = 0;
        } else if ((state.sourceBuffer || state.decoder) && state.socket && state.socket.readyState === WebSocket.OPEN) {
            state.healthyTicks += 1;
            state.degradeTicks = 0;
        }

        if (state.degradeTicks >= 2) {
            if (!changeFpsStep(-1)) changeBitrateStep(-1);
        } else if (state.healthyTicks >= 24 && state.queue.length <= 2) {
            if (nearestBitrateStep(state.bitrateScale) < 1) {
                changeBitrateStep(1);
            } else {
                changeFpsStep(1);
            }
        }
    }

    function startQualityMonitor(transportId) {
        if (state.qualityTimer) clearInterval(state.qualityTimer);
        state.qualityTimer = setInterval(function () {
            updateQuality(transportId);
        }, 1000);
    }

    function attachMedia(socket, codec, mime, transportId) {
        var panel = ensurePanel();
        var video = panel.querySelector('.alas-live-preview-video');
        var canvas = panel.querySelector('.alas-live-preview-canvas');
        if (!state.open || transportId !== state.transportId) {
            try { socket.close(); } catch (e) { }
            return;
        }

        video.style.display = 'block';
        canvas.style.display = 'none';
        state.socket = socket;
        state.mediaSource = new MediaSource();
        state.objectUrl = URL.createObjectURL(state.mediaSource);
        video.src = state.objectUrl;
        video.onwaiting = video.onstalled = function () {
            state.videoPressure = true;
        };
        video.onplaying = video.oncanplay = function () {
            state.videoPressure = false;
        };
        state.socket.onmessage = function (event) {
            if (!state.open || transportId !== state.transportId || state.socket !== socket) return;
            if (typeof event.data === 'string') {
                try {
                    var msg = JSON.parse(event.data);
                    if (msg.type === 'error') setStatus(msg.message);
                } catch (e) { }
                return;
            }
            state.lastChunkAt = Date.now();
            if (!state.firstChunkAt) state.firstChunkAt = state.lastChunkAt;
            state.queue.push(event.data);
            setStatus('');
            appendNext(transportId);
        };

        state.mediaSource.addEventListener('sourceopen', function () {
            if (!state.open || transportId !== state.transportId || state.socket !== socket) {
                return;
            }
            if (!MediaSource.isTypeSupported(mime)) {
                setStatus(codec.toUpperCase() + ' 当前浏览器不支持');
                cleanupTransport();
                return;
            }
            state.reconnectingForQuality = false;
            state.sourceBuffer = state.mediaSource.addSourceBuffer(mime);
            state.sourceBuffer.mode = 'segments';
            state.sourceBuffer.onupdateend = function () {
                appendNext(transportId);
            };
            startQualityMonitor(transportId);
            appendNext(transportId);
            state.socket.onerror = function () {
                if (transportId === state.transportId) setStatus('实时截图连接错误');
            };
            state.socket.onclose = function () {
                if (state.open && transportId === state.transportId) setStatus('实时截图已断开');
            };
        }, { once: true });
    }

    function getSocketCandidates() {
        var scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
        var query = '?instance=' + encodeURIComponent(state.instance) +
            '&codec=' + encodeURIComponent(state.codec) +
            '&mode=' + encodeURIComponent(state.mode) +
            '&fps=' + encodeURIComponent(state.fps) +
            '&width=' + encodeURIComponent(state.width) +
            '&bitrate_scale=' + encodeURIComponent(state.bitrateScale.toFixed(2));
        var candidates = [scheme + location.host + '/ws/live_screenshot' + query];
        var pathParts = location.pathname.split('/').filter(Boolean);
        var firstPart = pathParts.length ? pathParts[0] : '';

        // Alas 远程访问入口通常是 /{sock_name}/...，其中 sock_name 为 8+ 位小写字母数字。
        if (/^[a-z0-9]{8,}$/.test(firstPart)) {
            candidates.unshift(scheme + location.host + '/' + firstPart + '/ws/live_screenshot' + query);
        }

        return candidates;
    }

    function start(instance, codec, keepBitrate) {
        var panel = ensurePanel();
        var qualityReconnect = state.reconnectingForQuality;
        cleanupTransport();
        state.open = true;
        state.instance = instance || 'alas';
        state.codec = 'h264';
        try { localStorage.removeItem('alas_live_preview_codec'); } catch (e) { }
        if (!keepBitrate) {
            state.bitrateScale = 1;
            state.fps = 60;
            state.mode = 'auto';
            state.lastReconnectAt = Date.now();
        }
        state.reconnectingForQuality = qualityReconnect && keepBitrate;
        panel.style.display = 'block';
        setStatus('连接中');
        var transportId = state.transportId;
        var candidates = getSocketCandidates();
        var attempt = 0;

        function connectNext() {
            if (!state.open || transportId !== state.transportId) return;
            if (attempt >= candidates.length) {
                setStatus('实时截图连接失败');
                return;
            }

            var socket = new WebSocket(candidates[attempt++]);
            var ready = false;
            var advanced = false;
            function advance() {
                if (advanced) return;
                advanced = true;
                connectNext();
            }
            socket.binaryType = 'arraybuffer';
            socket.onmessage = function (event) {
                if (transportId !== state.transportId) return;
                if (typeof event.data !== 'string') return;
                var msg;
                try { msg = JSON.parse(event.data); } catch (e) { return; }
                if (msg.type === 'ready') {
                    ready = true;
                    state.fps = msg.fps || state.fps;
                    state.videoWidth = msg.width || state.videoWidth;
                    state.videoHeight = msg.height || state.videoHeight;
                    if (msg.maxrate) {
                        state.maxrate = msg.maxrate;
                        setStatus('连接中，' + (msg.mode || 'preview') + '，' + state.fps + ' FPS，码率上限 ' + msg.maxrate);
                    }
                    if (msg.format === 'raw_h264') {
                        attachRawH264(socket, msg, transportId);
                    } else {
                        attachMedia(socket, state.codec, msg.mime, transportId);
                    }
                } else if (msg.type === 'error') {
                    setStatus(msg.message);
                    socket.close();
                }
            };
            socket.onerror = function () {
                if (!ready) advance();
            };
            socket.onclose = function () {
                if (!state.open || transportId !== state.transportId) return;
                if (!ready && !state.socket) {
                    advance();
                } else if (ready && state.socket === socket) {
                    setStatus('实时截图已断开');
                }
            };
        }

        connectNext();
    }

    function startControl() {
        if (state.controlSocket || !state.open) return;
        var url = getWebSocketBase('/ws/live_control') + '?instance=' + encodeURIComponent(state.instance);
        var socket = new WebSocket(url);
        state.controlReady = false;
        state.controlSocket = socket;
        socket.onopen = function () {
            state.controlReady = true;
            setStatus('全屏控制已开启');
            flushControlQueue();
        };
        socket.onerror = function () {
            state.controlReady = false;
            setStatus('控制连接错误');
        };
        socket.onclose = function () {
            state.controlReady = false;
            if (state.controlSocket === socket) state.controlSocket = null;
        };
        socket.onmessage = function (event) {
            if (typeof event.data !== 'string') return;
            try {
                var msg = JSON.parse(event.data);
                if (msg.type === 'error') setStatus(msg.message);
            } catch (e) { }
        };
    }

    function stopControl() {
        state.controlReady = false;
        state.controlQueue = [];
        if (state.controlSocket) {
            state.controlSocket.onclose = null;
            state.controlSocket.onerror = null;
            state.controlSocket.onmessage = null;
            try { state.controlSocket.close(); } catch (e) { }
            state.controlSocket = null;
        }
    }

    function sendControl(payload) {
        if (!state.open) return;
        if (!state.controlSocket) startControl();
        if (!state.controlSocket || state.controlSocket.readyState !== WebSocket.OPEN) {
            state.controlQueue.push(payload);
            state.controlQueue = state.controlQueue.slice(-16);
            return;
        }
        try {
            state.controlSocket.send(JSON.stringify(payload));
        } catch (e) {
            setStatus(e.message || e);
        }
    }

    function flushControlQueue() {
        var queue = state.controlQueue.splice(0);
        for (var i = 0; i < queue.length; i += 1) {
            sendControl(queue[i]);
        }
    }

    function focusMobileKeyboard() {
        var panel = ensurePanel();
        var input = panel.querySelector('.alas-live-preview-keyboard-input');
        if (!input) return;
        input.value = '';
        input.focus({ preventScroll: true });
        setStatus('手机键盘已呼出');
    }

    function handleSystemAction(action) {
        if (action === 'keyboard') {
            focusMobileKeyboard();
            return;
        }
        sendControl({ type: action });
    }

    function bindSystemButtons(panel) {
        var buttons = panel.querySelectorAll('[data-live-control]');
        buttons.forEach(function (button) {
            button.addEventListener('click', function (event) {
                event.preventDefault();
                handleSystemAction(button.getAttribute('data-live-control'));
            });
        });
    }

    function bindMobileKeyboard(panel) {
        var input = panel.querySelector('.alas-live-preview-keyboard-input');
        if (!input) return;
        input.addEventListener('compositionstart', function () {
            state.keyboardComposing = true;
        });
        input.addEventListener('compositionend', function () {
            state.keyboardComposing = false;
            if (input.value) {
                sendControl({ type: 'text', text: input.value });
                input.value = '';
            }
        });
        input.addEventListener('input', function () {
            if (state.keyboardComposing || !input.value) return;
            sendControl({ type: 'text', text: input.value });
            input.value = '';
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Backspace') {
                sendControl({ type: 'key', key: 'Backspace' });
                event.preventDefault();
            } else if (event.key === 'Enter') {
                sendControl({ type: 'key', key: 'Enter' });
                input.value = '';
                event.preventDefault();
            } else if (event.key === 'Escape') {
                sendControl({ type: 'back' });
                event.preventDefault();
            }
        });
    }

    function videoPointFromEvent(event) {
        var panel = ensurePanel();
        var video = panel.querySelector('.alas-live-preview-video');
        var canvas = panel.querySelector('.alas-live-preview-canvas');
        var target = canvas.style.display === 'block' ? canvas : video;
        var rect = target.getBoundingClientRect();
        var contentRatio = state.videoWidth / state.videoHeight;
        var rectRatio = rect.width / rect.height;
        var left = rect.left;
        var top = rect.top;
        var width = rect.width;
        var height = rect.height;

        if (rectRatio > contentRatio) {
            width = rect.height * contentRatio;
            left += (rect.width - width) / 2;
        } else if (rectRatio < contentRatio) {
            height = rect.width / contentRatio;
            top += (rect.height - height) / 2;
        }

        var px = event.clientX - left;
        var py = event.clientY - top;
        if (px < 0 || py < 0 || px > width || py > height) return null;
        return {
            x: Math.round(px / width * 1280),
            y: Math.round(py / height * 720)
        };
    }

    function onPointerDown(event) {
        if (!state.fullscreenControl) return;
        var point = videoPointFromEvent(event);
        if (!point) return;
        event.preventDefault();
        state.pointerDown = {
            x: point.x,
            y: point.y,
            time: Date.now(),
            pointerId: event.pointerId
        };
        state.pointerMoves = 0;
        try { event.currentTarget.setPointerCapture(event.pointerId); } catch (e) { }
    }

    function onPointerMove(event) {
        if (!state.fullscreenControl || !state.pointerDown) return;
        state.pointerMoves += 1;
        event.preventDefault();
    }

    function onPointerUp(event) {
        if (!state.fullscreenControl || !state.pointerDown) return;
        var point = videoPointFromEvent(event);
        var down = state.pointerDown;
        state.pointerDown = null;
        event.preventDefault();
        if (!point) return;
        var dx = point.x - down.x;
        var dy = point.y - down.y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < 8 && state.pointerMoves < 3) {
            sendControl({ type: 'tap', x: point.x, y: point.y });
        } else {
            sendControl({
                type: 'drag',
                start: { x: down.x, y: down.y },
                end: { x: point.x, y: point.y },
                duration_ms: Math.max(80, Date.now() - down.time)
            });
        }
    }

    function onKeyDown(event) {
        if (!state.fullscreenControl) return;
        if (event.ctrlKey || event.altKey || event.metaKey) return;
        if (event.key && event.key.length === 1) {
            sendControl({ type: 'text', text: event.key });
        } else if (event.key === 'Escape') {
            sendControl({ type: 'back' });
        } else {
            sendControl({ type: 'key', key: event.key });
        }
        event.preventDefault();
    }

    function bindControlEvents(enable) {
        var panel = ensurePanel();
        var video = panel.querySelector('.alas-live-preview-video');
        var canvas = panel.querySelector('.alas-live-preview-canvas');
        var targets = [video, canvas];
        if (enable) {
            targets.forEach(function (target) {
                target.addEventListener('pointerdown', onPointerDown);
                target.addEventListener('pointermove', onPointerMove);
                target.addEventListener('pointerup', onPointerUp);
                target.addEventListener('pointercancel', onPointerUp);
            });
            document.addEventListener('keydown', onKeyDown, true);
        } else {
            targets.forEach(function (target) {
                target.removeEventListener('pointerdown', onPointerDown);
                target.removeEventListener('pointermove', onPointerMove);
                target.removeEventListener('pointerup', onPointerUp);
                target.removeEventListener('pointercancel', onPointerUp);
            });
            document.removeEventListener('keydown', onKeyDown, true);
        }
    }

    function lockLandscape() {
        var orientation = screen.orientation || screen.mozOrientation || screen.msOrientation;
        if (!orientation || typeof orientation.lock !== 'function') return;
        try {
            var result = orientation.lock('landscape');
            if (result && typeof result.catch === 'function') {
                result.catch(function () { });
            }
        } catch (e) { }
    }

    function unlockOrientation() {
        var orientation = screen.orientation || screen.mozOrientation || screen.msOrientation;
        if (!orientation || typeof orientation.unlock !== 'function') return;
        try {
            orientation.unlock();
        } catch (e) { }
    }

    function enterFullscreenControl() {
        var panel = ensurePanel();
        if (!panel.requestFullscreen) {
            setStatus('当前浏览器不支持全屏控制');
            return;
        }
        panel.requestFullscreen().then(function () {
            lockLandscape();
            state.fullscreenControl = true;
            panel.classList.add('alas-live-preview-fullscreen-mode');
            bindControlEvents(true);
            startControl();
        }).catch(function (e) {
            setStatus((e && e.message) || '进入全屏失败');
        });
    }

    function exitFullscreenControl(exitFullscreen) {
        if (!state.fullscreenControl && !document.fullscreenElement) return;
        state.fullscreenControl = false;
        bindControlEvents(false);
        stopControl();
        unlockOrientation();
        var panel = ensurePanel();
        panel.classList.remove('alas-live-preview-fullscreen-mode');
        if (exitFullscreen && document.fullscreenElement === panel) {
            try { document.exitFullscreen(); } catch (e) { }
        }
    }

    document.addEventListener('fullscreenchange', function () {
        var panel = document.getElementById('alas-live-preview');
        if (!panel) return;
        if (document.fullscreenElement !== panel) {
            exitFullscreenControl(true);
        }
    });

    window.alasStartLivePreview = function (instance, codec) {
        start(instance, codec);
    };

    window.alasStopLivePreview = function () {
        state.open = false;
        cleanupTransport();
        var panel = ensurePanel();
        panel.style.display = 'none';
    };

    window.alasToggleLivePreview = function (instance) {
        if (state.open) {
            window.alasStopLivePreview();
        } else {
            window.alasStartLivePreview(instance, state.codec);
        }
    };
})();

// ============================================================
// 公告系统
// ============================================================
(function () {
    var STORAGE_KEY = 'alas_shown_announcements';

    window.alasGetShownAnnouncements = function () {
        try {
            var stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            return [];
        }
    };

    window.alasMarkAnnouncementShown = function (announcementId) {
        try {
            var shown = window.alasGetShownAnnouncements();
            if (shown.indexOf(announcementId) === -1) {
                shown.push(announcementId);
                localStorage.setItem(STORAGE_KEY, JSON.stringify(shown));
            }
        } catch (e) { }
    };

    window.alasHasBeenShown = function (announcementId) {
        var shown = window.alasGetShownAnnouncements();
        return shown.indexOf(announcementId) !== -1;
    };

    window.alasShowAnnouncement = function (title, content, announcementId, url, force) {
        if ((!force && window.alasHasBeenShown(announcementId)) || document.getElementById('alas-announcement-modal')) {
            return;
        }

        // Create modal overlay
        var overlay = document.createElement('div');
        overlay.id = 'alas-announcement-modal';
        overlay.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;background:rgba(0,0,0,0.5);z-index:100000;display:flex;justify-content:center;align-items:center;';

        // Create modal content
        var modal = document.createElement('div');
        var isWeb = !!url;

        if (isWeb) {
            // Web page style: larger, fixed height
            modal.style.cssText = 'background:#fff;border-radius:12px;padding:16px;width:95%;max-width:1200px;height:85vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.3);';
        } else {
            // Text style: automatic height, narrower
            modal.style.cssText = 'background:#fff;border-radius:12px;padding:24px;max-width:500px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);';
        }

        // Title
        var titleEl = document.createElement('h3');
        titleEl.textContent = title;
        titleEl.style.cssText = 'margin:0 0 12px 0;font-size:1.25rem;color:#333;border-bottom:2px solid #4fc3f7;padding-bottom:8px;flex-shrink:0;';

        modal.appendChild(titleEl);

        // Content (Text or Iframe)
        if (isWeb) {
            var iframe = document.createElement('iframe');
            iframe.src = sanitizeUrl(url);
            iframe.style.cssText = 'flex:1;border:none;width:100%;background:#f5f5f5;border-radius:4px;';
            modal.appendChild(iframe);
        } else {
            var contentEl = document.createElement('div');
            contentEl.textContent = content;
            contentEl.style.cssText = 'font-size:1rem;color:#555;line-height:1.6;margin-bottom:20px;white-space:pre-wrap;';
            modal.appendChild(contentEl);
        }

        // Close button area
        var btnContainer = document.createElement('div');
        btnContainer.style.cssText = 'margin-top:16px;text-align:center;flex-shrink:0;';

        var closeBtn = document.createElement('button');
        closeBtn.textContent = '确认';
        closeBtn.style.cssText = 'background:linear-gradient(90deg,#00b894,#0984e3);color:#fff;border:none;padding:10px 32px;border-radius:6px;cursor:pointer;font-size:1rem;display:inline-block;';
        closeBtn.onmouseover = function () { closeBtn.style.opacity = '0.9'; };
        closeBtn.onmouseout = function () { closeBtn.style.opacity = '1'; };
        closeBtn.onclick = function () {
            window.alasMarkAnnouncementShown(announcementId);
            overlay.remove();
        };

        btnContainer.appendChild(closeBtn);
        modal.appendChild(btnContainer);

        overlay.appendChild(modal);

        // Close on overlay click
        overlay.onclick = function (e) {
            if (e.target === overlay) {
                window.alasMarkAnnouncementShown(announcementId);
                overlay.remove();
            }
        };

        document.body.appendChild(overlay);

        // Apply dark theme if needed
        try {
            var isDark = document.body.classList.contains('pywebio-dark') ||
                document.documentElement.getAttribute('data-theme') === 'dark' ||
                localStorage.getItem('Theme') === 'dark';
            if (isDark) {
                modal.style.background = '#2d3436';
                titleEl.style.color = '#dfe6e9';
                if (!isWeb) {
                    // contentEl only exists in text mode
                    var c = modal.querySelector('div[style*="font-size:1rem"]');
                    if (c) c.style.color = '#b2bec3';
                }
            }
        } catch (e) { }
    };
})();
