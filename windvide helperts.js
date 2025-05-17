// ==UserScript==
// @name         Widevine Download Helper+++
// @namespace    http://tampermonkey.net/
// @version      1.8.4 // Version increment
// @description  Displays PSSH, License URL, Headers, MPD URL in top window. Auto-fetches key. Click to copy.
// @author       YourName (modified from cramer's EME Logger)
// @match        *://*/*
 // @run-at       document-start
// @all-frames   true
// @grant        GM_addStyle
// @grant        GM_setClipboard
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @downloadURL  none
// @updateURL    none
// ==/UserScript==

(async () => {
// --- Service Worker Message Interception (EXPERIMENTAL) ---
try {
    if ('serviceWorker' in navigator) {
        // Listen for messages from the service worker
        navigator.serviceWorker.addEventListener('message', function(event) {
            if (event && event.data) {
                const dataStr = typeof event.data === 'string' ? event.data : JSON.stringify(event.data);
                if (dataStr && (dataStr.toLowerCase().includes('license') || dataStr.toLowerCase().includes('mpd') || dataStr.toLowerCase().includes('auth'))) {
                    console.log(SCRIPT_TITLE + ' [SW]: Message from service worker:', event.data);
                }
            }
        });
        // Patch postMessage to log outgoing messages to the service worker
        if (navigator.serviceWorker.controller && navigator.serviceWorker.controller.postMessage) {
            const origPostMessage = navigator.serviceWorker.controller.postMessage;
            navigator.serviceWorker.controller.postMessage = function(...args) {
                try {
                    const msgStr = typeof args[0] === 'string' ? args[0] : JSON.stringify(args[0]);
                    if (msgStr && (msgStr.toLowerCase().includes('license') || msgStr.toLowerCase().includes('mpd') || msgStr.toLowerCase().includes('auth'))) {
                        console.log(SCRIPT_TITLE + ' [SW]: postMessage to service worker:', args[0]);
                    }
                } catch (e) {}
                return origPostMessage.apply(this, args);
            };
        }
    }
} catch (e) {
    console.warn(SCRIPT_TITLE + ' [SW]: Failed to hook service worker messaging:', e);
}
    'use strict';

    const SCRIPT_TITLE = 'Widevine Helper +++';
    const LICENSE_KEYWORDS = ['widevine', 'licence', 'license', 'getlicence', 'vdocipher', 'auth'];
    const MPD_KEYWORD = 'mpd';
    const DECRYPT_API_URL = 'https://cdrm-project.com/api/decrypt';
    const PYTHON_APP_URL = 'http://localhost:12345/submit_data';
    const MESSAGE_TYPE_PREFIX = 'WIDEVINE_HELPER_DATA_';

    // --- Data Storage (primarily for top window) ---
    let currentPssh = 'Waiting...';
    let currentLicenseUrl = 'Waiting...';
    let rawHeadersObject = {};
    let currentMpdUrl = 'Waiting...';
    let currentDecryptionKey = 'Waiting...';

    let hasDetectedLicense = false;
    let hasDetectedMpd = false;

    // --- Key Fetching State (for top window) ---
    let isFetchingKey = false;
    let lastKeyFetchSignature = null;

    // --- Python App Communication State (for top window) ---
    let lastSentKeyToPython = null;
    let lastSentMpdUrlToPython = null;

    // --- UI Popup Elements & State (for top window) ---
    let popup = null;
    let popupTitleElement, psshContainer, licenseUrlContainer, headersContainer, mpdUrlContainer, decryptionKeyContainer;
    let psshValueElement, licenseUrlValueElement, headersValueElement, mpdUrlValueElement, decryptionKeyValueElement;
    let copyFeedbackElement = null;
    let copyTimeoutId = null;
    let isCollapsed = GM_getValue('popupCollapsed', false);
    const originalTitleText = 'Widevine Helper +++';

    const IS_TOP_WINDOW = window.self === window.top;
    const FRAME_ID = IS_TOP_WINDOW ? 'TOP' : `IFRAME ${window.location.pathname.substring(window.location.pathname.length - 20).replace(/\//g, '_')}`;

    // DEBUG: Log when running in iframe
    if (!IS_TOP_WINDOW) {
        console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Script running in iframe. Location: ${window.location.href}`);
        // Send a test message to top window
        try {
            window.top.postMessage({ type: MESSAGE_TYPE_PREFIX + 'IFRAME_TEST', payload: { url: window.location.href } }, '*');
        } catch (e) {
            console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Failed to send test message to top window.`, e);
        }
    }
// --- IFRAME: Warn if no relevant requests are intercepted ---
if (!IS_TOP_WINDOW) {
    let interceptedRelevantRequest = false;
    const origProcessNetworkRequest = processNetworkRequest;
    processNetworkRequest = function(url, headersObject, sourceType) {
        origProcessNetworkRequest(url, headersObject, sourceType);
        // Check for MPD or license keywords
        const urlLower = url ? url.toLowerCase() : "";
        if (
            (urlLower.includes(".mpd") || urlLower.includes("license") || urlLower.includes("licence") || urlLower.includes("auth")) &&
            typeof url === "string"
        ) {
            interceptedRelevantRequest = true;
        }
    };
    // After 5 seconds, if no relevant requests, log a warning
    setTimeout(() => {
        if (!interceptedRelevantRequest) {
            console.warn(`${SCRIPT_TITLE} [${FRAME_ID}]: No MPD or license requests intercepted in this iframe. The player may use a custom network stack or service worker.`);
        }
    }, 5000);
}

    // --- Helper Functions ---
    const b64 = { encode: b => btoa(String.fromCharCode(...new Uint8Array(b))) };
    const fnproxy = (object, func) => new Proxy(object, { apply: func });
    const proxy = (object, key, func) => {
        const descriptor = Object.getOwnPropertyDescriptor(object, key);
        if (descriptor && descriptor.configurable && typeof object[key] === 'function') {
            try {
                Object.defineProperty(object, key, {
                    value: fnproxy(object[key], func),
                    writable: descriptor.writable !== undefined ? descriptor.writable : true,
                    enumerable: descriptor.enumerable !== undefined ? descriptor.enumerable : true,
                    configurable: true
                });
                return true;
            } catch (e) { console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Failed to proxy ${key}:`, e); return false; }
        }
        return false;
    };

    function _extractNameFromUrl(urlString) {
        if (typeof urlString !== 'string' || !urlString) return null;
        try {
            const urlObj = new URL(urlString);
            const pathnameDecoded = decodeURIComponent(urlObj.pathname);
            const pathParts = pathnameDecoded.split('/').filter(part => part.length > 0);
            return pathParts.length > 0 ? pathParts[pathParts.length - 1] : null;
        } catch (e) { return null; }
    }

    // --- Communication for IFRAMES to send data to TOP window ---
    function sendDataToTop(type, payload) {
        if (!IS_TOP_WINDOW) {
            try {
                const message = { type: MESSAGE_TYPE_PREFIX + type, payload: payload, sourceFrameUrl: window.location.href };
                window.top.postMessage(message, '*');
                // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Sent ${type} to top:`, payload);
            } catch (e) { console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Error sending ${type} to top:`, e); }
        }
    }

    // --- UI Functions (only effective in TOP window) ---
    function showCopyFeedback(targetElement) { /* ... same ... */
        if (!IS_TOP_WINDOW || !copyFeedbackElement || !popup) return;
        if (copyTimeoutId) clearTimeout(copyTimeoutId);
        const popupRect = popup.getBoundingClientRect();
        const topPos = isCollapsed ? (window.innerHeight / 2) : (popupRect.bottom + 5);
        const leftPos = isCollapsed ? (window.innerWidth / 2) : popupRect.left;
        copyFeedbackElement.style.top = `${topPos}px`; copyFeedbackElement.style.left = `${leftPos}px`;
        copyFeedbackElement.textContent = 'Copied!'; copyFeedbackElement.style.display = 'block';
        copyFeedbackElement.style.transform = isCollapsed ? 'translate(-50%, -50%)' : 'none';
        copyTimeoutId = setTimeout(() => { if (copyFeedbackElement) copyFeedbackElement.style.display = 'none'; copyTimeoutId = null; }, 1500);
    }
    function addCopyListener(element, textToCopyProvider) { /* ... same ... */
        if (!IS_TOP_WINDOW || !element) return;
        element.onclick = (e) => {
            e.preventDefault(); e.stopPropagation();
            const currentTextToCopy = typeof textToCopyProvider === 'function' ? textToCopyProvider() : textToCopyProvider;
            if (currentTextToCopy && currentTextToCopy !== 'Waiting...' && !currentTextToCopy.startsWith('[Error') && !currentTextToCopy.startsWith('Fetching key...')) {
                GM_setClipboard(currentTextToCopy, 'text'); showCopyFeedback(element);
            }
        };
        const textToCopy = typeof textToCopyProvider === 'function' ? textToCopyProvider() : textToCopyProvider;
        if (textToCopy && textToCopy !== 'Waiting...' && !textToCopy.startsWith('[Error') && !textToCopy.startsWith('Fetching key...')) {
            element.style.cursor = 'pointer'; element.style.textDecoration = 'underline'; element.title = 'Click to copy';
        } else {
            element.style.cursor = 'default'; element.style.textDecoration = 'none'; element.title = '';
        }
    }
    function toggleCollapse() { /* ... same ... */
        if (!IS_TOP_WINDOW || !popup) return;
        isCollapsed = !isCollapsed; GM_setValue('popupCollapsed', isCollapsed);
        popup.classList.toggle('widevine-popup-collapsed', isCollapsed);
        if (popupTitleElement) {
            popupTitleElement.textContent = isCollapsed ? '●' : originalTitleText;
            popupTitleElement.title = isCollapsed ? 'Click to expand' : 'Click to collapse';
            if (!isCollapsed) updatePopupUIContent();
        }
        if (isCollapsed && copyFeedbackElement) copyFeedbackElement.style.display = 'none';
    }
    function createPopup() { /* ... same ... */
        if (!IS_TOP_WINDOW || popup) return;
        console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Creating Popup UI.`);
        popup = document.createElement('div'); popup.id = 'widevine-helper-popup';
        if (isCollapsed) popup.classList.add('widevine-popup-collapsed');
        popupTitleElement = document.createElement('div'); popup.appendChild(popupTitleElement);
        psshContainer = document.createElement('div'); psshValueElement = document.createElement('span'); psshContainer.appendChild(psshValueElement); popup.appendChild(psshContainer);
        licenseUrlContainer = document.createElement('div'); licenseUrlValueElement = document.createElement('span'); licenseUrlContainer.appendChild(licenseUrlValueElement); popup.appendChild(licenseUrlContainer);
        headersContainer = document.createElement('div'); headersValueElement = document.createElement('span'); headersValueElement.style.whiteSpace = 'pre-wrap'; headersContainer.appendChild(headersValueElement); popup.appendChild(headersContainer);
        mpdUrlContainer = document.createElement('div'); mpdUrlValueElement = document.createElement('span'); mpdUrlContainer.appendChild(mpdUrlValueElement); popup.appendChild(mpdUrlContainer);
        decryptionKeyContainer = document.createElement('div'); decryptionKeyValueElement = document.createElement('span'); decryptionKeyContainer.appendChild(decryptionKeyValueElement); popup.appendChild(decryptionKeyContainer);
        popupTitleElement.textContent = isCollapsed ? '●' : originalTitleText; popupTitleElement.title = isCollapsed ? 'Click to expand' : 'Click to collapse';
        popupTitleElement.style.fontWeight = 'bold'; popupTitleElement.style.marginBottom = '8px'; popupTitleElement.style.borderBottom = '1px solid #ccc'; popupTitleElement.style.paddingBottom = '5px'; popupTitleElement.style.cursor = 'pointer';
        popupTitleElement.onclick = toggleCollapse;
        psshContainer.innerHTML = '<b>PSSH:</b> '; psshContainer.appendChild(psshValueElement);
        licenseUrlContainer.innerHTML = '<b>License URL:</b> '; licenseUrlContainer.appendChild(licenseUrlValueElement);
        headersContainer.innerHTML = '<b>Headers:</b> '; headersContainer.appendChild(headersValueElement);
        mpdUrlContainer.innerHTML = '<b>MPD url:</b> '; mpdUrlContainer.appendChild(mpdUrlValueElement);
        decryptionKeyContainer.innerHTML = '<b>Decryption Key:</b> '; decryptionKeyContainer.appendChild(decryptionKeyValueElement);
        copyFeedbackElement = document.createElement('div'); copyFeedbackElement.id = 'widevine-copy-feedback'; copyFeedbackElement.style.display = 'none';
        (document.body || document.documentElement).appendChild(copyFeedbackElement);
        GM_addStyle(` #widevine-helper-popup { position: fixed; top: 10px; right: 10px; background-color: white; color: black; border: 1px solid #ccc; border-radius: 8px; padding: 12px; z-index: 2147483647 !important; font-family: sans-serif; font-size: 12px; max-width: 380px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); transition: all 0.2s ease-in-out; overflow: hidden; } #widevine-helper-popup > div { margin-bottom: 6px; transition: opacity 0.1s linear; word-break: break-all; } #widevine-helper-popup.widevine-popup-collapsed { width: 20px; height: 20px; padding: 0; border-radius: 50%; cursor: pointer; max-width: 20px; } #widevine-helper-popup.widevine-popup-collapsed > div:not(:first-child) { display: none; opacity: 0; } #widevine-helper-popup.widevine-popup-collapsed > div:first-child { text-align: center; line-height: 20px; margin-bottom: 0; border-bottom: none; padding-bottom: 0; font-size: 14px; color: #555; } #widevine-copy-feedback { position: fixed; background-color: #28a745; color: white; padding: 5px 10px; border-radius: 4px; font-size: 11px; z-index: 2147483647 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.2); transform: none; transition: top 0.1s, left 0.1s, transform 0.1s; } `);
        (document.body || document.documentElement).appendChild(popup);
        updatePopupUIContent();
    }
    function updatePopupUIContent() { /* ... same ... */
        if (!IS_TOP_WINDOW || !popup || isCollapsed) return;
        // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Updating popup content. PSSH: ${currentPssh.slice(0,15)}..., LicURL: ${currentLicenseUrl}, MPD: ${currentMpdUrl}, Key: ${currentDecryptionKey.slice(0,15)}...`);
        if (psshValueElement) { psshValueElement.textContent = currentPssh; addCopyListener(psshValueElement, () => currentPssh); }
        if (licenseUrlValueElement) { licenseUrlValueElement.textContent = currentLicenseUrl; addCopyListener(licenseUrlValueElement, () => currentLicenseUrl); }
        if (headersValueElement) {
            let displayHeadersString = "{}"; let headersToCopy = null;
            if (hasDetectedLicense) {
                if (Object.keys(rawHeadersObject).length > 0) { try { displayHeadersString = JSON.stringify(rawHeadersObject, null, 2); headersToCopy = JSON.stringify(rawHeadersObject); } catch (e) { displayHeadersString = "[Error formatting headers]"; } }
                else { displayHeadersString = "{} (No specific request headers captured)"; headersToCopy = "{}"; }
            } else { displayHeadersString = "Waiting for license detection...";}
            headersValueElement.textContent = displayHeadersString; addCopyListener(headersValueElement, () => headersToCopy);
        }
        if (mpdUrlValueElement) { mpdUrlValueElement.textContent = currentMpdUrl; addCopyListener(mpdUrlValueElement, () => currentMpdUrl); }
        if (decryptionKeyValueElement) { decryptionKeyValueElement.textContent = currentDecryptionKey; addCopyListener(decryptionKeyValueElement, () => currentDecryptionKey); }
    }

    // MODIFICATION: Declare these functions (or stubs) before they are potentially called by updatePopupUIVisibility
    let checkAndFetchKey = () => {};
    let attemptSendDataToPython = () => {};
    // fetchDecryptionKey and sendDataToPythonApp are only called by the above, so they are fine inside the IS_TOP_WINDOW block

    function updatePopupUIVisibility() {
        if (!IS_TOP_WINDOW) return;
        const shouldShowPopup = hasDetectedLicense || hasDetectedMpd || (currentPssh !== 'Waiting...' && !currentPssh.startsWith('[Error'));
        if (!popup && shouldShowPopup) {
            if (document.body || document.readyState === 'interactive' || document.readyState === 'complete') createPopup();
            else { document.addEventListener('DOMContentLoaded', createPopup); return; }
        }
        if (popup && shouldShowPopup) {
            popup.style.display = '';
            if (!isCollapsed) updatePopupUIContent();
        }
        // These are now guaranteed to be defined (either as stubs or the real functions)
        checkAndFetchKey();
        attemptSendDataToPython();
    }

    // --- Data Handling for TOP window (from messages or direct capture) ---
    function _handleCapturedPssh(pssh) { /* ... same ... */
        if (!IS_TOP_WINDOW) return;
        if (pssh && currentPssh !== pssh) {
            currentPssh = pssh;
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: PSSH Updated: ${currentPssh.slice(0,40)}...`);
            updatePopupUIVisibility();
        }
    }
    function _handleCapturedLicense(url, headers) { /* ... same ... */
        if (!IS_TOP_WINDOW) return;
        if (url) {
            // let changed = currentLicenseUrl !== url || JSON.stringify(rawHeadersObject) !== JSON.stringify(headers);
            currentLicenseUrl = url;
            rawHeadersObject = { ...headers }; // Always update headers with the latest license URL
            hasDetectedLicense = true;
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: License Info Updated: URL=${currentLicenseUrl}`);
            updatePopupUIVisibility();
        }
    }
    function _handleCapturedMpd(url) { /* ... same ... */
        if (!IS_TOP_WINDOW) return;
        if (url) {
            // let changed = currentMpdUrl !== url;
            currentMpdUrl = url;
            hasDetectedMpd = true;
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: MPD Info Updated: URL=${currentMpdUrl}`);
            updatePopupUIVisibility();
        }
    }

    // --- Interception Logic (runs in all frames) ---
    if (typeof MediaKeySession !== 'undefined') { /* ... same ... */
        proxy(MediaKeySession.prototype, 'generateRequest', async function(_target, _this, _args) {
            const [, initData] = _args; let psshVal = "[No initData provided]";
            if (initData) { try { psshVal = b64.encode(initData); } catch (e) { psshVal = "[Error encoding PSSH]"; } }
            // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: PSSH Captured. PSSH: ${psshVal.slice(0,40)}...`);
            if (IS_TOP_WINDOW) _handleCapturedPssh(psshVal); else sendDataToTop('PSSH', { pssh: psshVal });
            return _target.apply(_this, _args);
        });
    }

    const originalFetch = window.fetch; /* ... same ... */
    window.fetch = async function(...args) {
        let url = args[0] instanceof Request ? args[0].url : args[0]; let headers = {};
        if (args[0] instanceof Request && args[0].headers) { try { for (const [k, v] of args[0].headers.entries()) headers[k.toLowerCase()] = v; } catch (e) {} }
        if (args[1] && args[1].headers) {
            if (args[1].headers instanceof Headers) { try { for (const [k, v] of args[1].headers.entries()) headers[k.toLowerCase()] = v; } catch (e) {} }
            else if (typeof args[1].headers === 'object') { Object.keys(args[1].headers).forEach(k => { if (args[1].headers[k] != null) headers[k.toLowerCase()] = String(args[1].headers[k]); }); }
        }
        processNetworkRequest(url, headers, "FETCH");
        return originalFetch.apply(this, args);
    };

    const originalXhrOpen = XMLHttpRequest.prototype.open; /* ... same ... */
    const originalXhrSend = XMLHttpRequest.prototype.send;
    const originalXhrSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
    const xhrData = new WeakMap();
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        let urlStr = typeof url === 'string' ? url : (url ? String(url) : null);
        if (urlStr) xhrData.set(this, { url: urlStr, method: method, headers: {} });
        else xhrData.set(this, { url: null, method: method, headers: {} });
        return originalXhrOpen.apply(this, [method, url, ...rest]);
    };
    XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
        const data = xhrData.get(this); if (data && data.headers && typeof header === 'string') data.headers[header.toLowerCase()] = String(value);
        return originalXhrSetRequestHeader.apply(this, [header, value]);
    };
    XMLHttpRequest.prototype.send = function(...args) {
        const data = xhrData.get(this);
        if (data && data.url) processNetworkRequest(data.url, data.headers || {}, "XHR");
        return originalXhrSend.apply(this, args);
    };

    function processNetworkRequest(url, headersObject, sourceType) {
        if (typeof url !== 'string' || !url) return;
        // Enhanced logging for debugging
        console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: [${sourceType}] Processing URL: ${url}`);
        const urlLower = url.toLowerCase();
        const name = _extractNameFromUrl(url);
        const nameLower = name ? name.toLowerCase() : null;
        let isLicenseMatch = false, isMpdMatch = false;

        // Log keyword checks for debugging
        for (const keyword of LICENSE_KEYWORDS) {
            if (urlLower.includes(keyword) || (nameLower && nameLower.includes(keyword))) {
                isLicenseMatch = true;
                console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Matched license keyword "${keyword}" in URL: ${url}`);
                break;
            }
        }
        if (urlLower.includes(".mpd") || (nameLower && nameLower.includes(MPD_KEYWORD))) {
            isMpdMatch = true;
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Matched MPD keyword in URL: ${url}`);
        }

        if (isLicenseMatch) {
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: License URL DETECTED: ${url}`);
            if (IS_TOP_WINDOW) _handleCapturedLicense(url, headersObject); else sendDataToTop('LICENSE_REQUEST', { url: url, headers: headersObject });
        }
        if (isMpdMatch) {
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: MPD URL DETECTED: ${url}`);
            if (IS_TOP_WINDOW) _handleCapturedMpd(url); else sendDataToTop('MPD_REQUEST', { url: url });
        }

        // Fallback: Log if nothing matched
        if (!isLicenseMatch && !isMpdMatch) {
            console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: No match for URL: ${url}`);
        }
    }

    // --- TOP Window Message Listener & Key Fetching/Python App Logic ---
    if (IS_TOP_WINDOW) {
        console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Initialized. Listening for iframe data. Version ${GM_info.script.version}`);
        window.addEventListener('message', function(event) { /* ... same ... */
            if (event.source === window || !event.data || typeof event.data.type !== 'string' || !event.data.type.startsWith(MESSAGE_TYPE_PREFIX)) return;
            const messageType = event.data.type.substring(MESSAGE_TYPE_PREFIX.length);
            const payload = event.data.payload;
            // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Received message from iframe ${event.data.sourceFrameUrl || 'UNKNOWN_SOURCE'}: Type=${messageType}`, payload);
            switch (messageType) {
                case 'PSSH': _handleCapturedPssh(payload.pssh); break;
                case 'LICENSE_REQUEST': _handleCapturedLicense(payload.url, payload.headers); break;
                case 'MPD_REQUEST': _handleCapturedMpd(payload.url); break;
            }
        });

        // MODIFICATION: Assign the actual functions if in the top window
        let fetchDecryptionKey; // Declare here for scope
        let sendDataToPythonApp; // Declare here for scope

        checkAndFetchKey = function() {
            if (currentPssh === 'Waiting...' || currentPssh.startsWith('[Error') ||
                currentLicenseUrl === 'Waiting...' || Object.keys(rawHeadersObject).length === 0 ||
                !hasDetectedLicense || isFetchingKey) {
                return;
            }
            const currentDataSignature = `${currentPssh}|${currentLicenseUrl}|${JSON.stringify(rawHeadersObject)}`;
            if (currentDataSignature === lastKeyFetchSignature) return;
            fetchDecryptionKey(currentPssh, currentLicenseUrl, rawHeadersObject, currentDataSignature);
        };

        fetchDecryptionKey = function(pssh, licUrl, headersObj, dataSignature) { /* ... same as before ... */
            isFetchingKey = true; lastKeyFetchSignature = dataSignature; currentDecryptionKey = 'Fetching key...';
            if(popup && !isCollapsed) updatePopupUIContent();
            const payload = { pssh: pssh, licurl: licUrl, headers: JSON.stringify(headersObj) };
            // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Attempting to fetch decryption key. Payload:`, payload);
            GM_xmlhttpRequest({ method: "POST", url: DECRYPT_API_URL, headers: { "Content-Type": "application/json" }, data: JSON.stringify(payload), timeout: 20000,
                onload: function(response) {
                    isFetchingKey = false;
                    try {
                        if (response.status >= 200 && response.status < 300) {
                            const jsonResponse = JSON.parse(response.responseText);
                            if (jsonResponse && jsonResponse.keys && Array.isArray(jsonResponse.keys) && jsonResponse.keys.length > 0 && jsonResponse.keys[0].key) currentDecryptionKey = jsonResponse.keys.map(k => k.key).join('; ');
                            else if (jsonResponse && typeof jsonResponse.message === 'string' && !jsonResponse.message.toLowerCase().includes("error")) currentDecryptionKey = jsonResponse.message;
                            else if (typeof response.responseText === 'string' && response.responseText.includes(':')) currentDecryptionKey = response.responseText;
                            else { currentDecryptionKey = `[API Error: ${jsonResponse.error || jsonResponse.message || 'Unknown format'}]`; lastKeyFetchSignature = null; }
                        } else { currentDecryptionKey = `[HTTP Error: ${response.status}] ${response.responseText.substring(0,100)}`; lastKeyFetchSignature = null; }
                    } catch (e) { currentDecryptionKey = "[Error: Invalid JSON from key API]"; lastKeyFetchSignature = null; }
                    // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Decryption key result:`, currentDecryptionKey);
                    if(popup && !isCollapsed) updatePopupUIContent(); attemptSendDataToPython();
                },
                onerror: function() { isFetchingKey = false; currentDecryptionKey = "[Error: Network fail for key API]"; lastKeyFetchSignature = null; if(popup && !isCollapsed) updatePopupUIContent(); },
                ontimeout: function() { isFetchingKey = false; currentDecryptionKey = "[Error: Key API Timeout]"; lastKeyFetchSignature = null; if(popup && !isCollapsed) updatePopupUIContent(); }
            });
        };

        attemptSendDataToPython = function() {
            const keyReady = currentDecryptionKey && currentDecryptionKey !== 'Waiting...' && !currentDecryptionKey.startsWith('[Error') && !currentDecryptionKey.startsWith('Fetching key...');
            const mpdReady = currentMpdUrl && currentMpdUrl !== 'Waiting...';
            if (keyReady && mpdReady && (currentDecryptionKey !== lastSentKeyToPython || currentMpdUrl !== lastSentMpdUrlToPython)) {
                sendDataToPythonApp(currentDecryptionKey, currentMpdUrl);
            }
        };

        sendDataToPythonApp = function(key, mpdUrl) { /* ... same as before ... */
            // console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Sending data to Python app. MPD: ${mpdUrl}, Key: ${key}`);
            GM_xmlhttpRequest({ method: "POST", url: PYTHON_APP_URL, headers: { "Content-Type": "application/json" }, data: JSON.stringify({ mpdUrl: mpdUrl, key: key }), timeout: 5000,
                onload: function(response) {
                    if (response.status >= 200 && response.status < 300) { /* console.log(`${SCRIPT_TITLE} [${FRAME_ID}]: Successfully sent data to Python app.`); */ lastSentKeyToPython = key; lastSentMpdUrlToPython = mpdUrl; }
                    // else console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Error sending data to Python app. Status: ${response.status}`);
                },
                // onerror: function() { console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Network error sending data to Python app.`); },
                // ontimeout: function() { console.error(`${SCRIPT_TITLE} [${FRAME_ID}]: Timeout sending data to Python app.`); }
            });
        };
        updatePopupUIVisibility(); // Initial check
    }
    // For IFRAMEs, checkAndFetchKey and attemptSendDataToPython remain as stubs (assigned earlier)
})();