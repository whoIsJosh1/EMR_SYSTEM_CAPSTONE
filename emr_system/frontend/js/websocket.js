// ============================================================
// websocket.js
// CENTRAL EMR REAL-TIME WEBSOCKET
// ============================================================

(function () {

    let socket = null;
    let reconnectTimer = null;
    let heartbeatTimer = null;

    let manuallyClosed = false;

    const RECONNECT_DELAY = 3000;
    const HEARTBEAT_INTERVAL = 25000;


    // ========================================================
    // GET WEBSOCKET URL
    // ========================================================

    function getWebSocketUrl() {

        const protocol =
            window.location.protocol === "https:"
                ? "wss:"
                : "ws:";

        return `${protocol}//${window.location.host}/ws`;
    }


    // ========================================================
    // CONNECT
    // ========================================================

    function connectWebSocket() {

        if (manuallyClosed) {
            return;
        }

        if (
            socket &&
            (
                socket.readyState === WebSocket.OPEN ||
                socket.readyState === WebSocket.CONNECTING
            )
        ) {
            return;
        }

        const url = getWebSocketUrl();

        console.log(
            "🔌 Connecting to EMR WebSocket:",
            url
        );

        socket = new WebSocket(url);


        // ====================================================
        // OPEN
        // ====================================================

        socket.onopen = function () {

            console.log(
                "🟢 EMR WebSocket connected"
            );

            startHeartbeat();

            window.dispatchEvent(
                new CustomEvent(
                    "emr:websocket-status",
                    {
                        detail: {
                            connected: true
                        }
                    }
                )
            );
        };


        // ====================================================
        // MESSAGE
        // ====================================================

        socket.onmessage = function (event) {

            let data;

            try {
                data = JSON.parse(event.data);
            }

            catch (error) {

                console.warn(
                    "⚠️ Invalid WebSocket message:",
                    event.data
                );

                return;
            }


            console.log(
                "📡 EMR WebSocket event:",
                data
            );


            // Ignore heartbeat response
            if (data.type === "pong") {
                return;
            }


            // ------------------------------------------------
            // GLOBAL EVENT
            // ------------------------------------------------

            window.dispatchEvent(
                new CustomEvent(
                    "emr:websocket",
                    {
                        detail: data
                    }
                )
            );


            // ------------------------------------------------
            // INVENTORY COMPATIBILITY
            // ------------------------------------------------

            if (
                typeof window.handleInventoryWebSocketMessage
                === "function"
            ) {

                window.handleInventoryWebSocketMessage(
                    data
                );
            }

        };


        // ====================================================
        // CLOSE
        // ====================================================

        socket.onclose = function () {

            console.warn(
                "🔴 EMR WebSocket disconnected"
            );

            stopHeartbeat();

            window.dispatchEvent(
                new CustomEvent(
                    "emr:websocket-status",
                    {
                        detail: {
                            connected: false
                        }
                    }
                )
            );


            scheduleReconnect();
        };


        // ====================================================
        // ERROR
        // ====================================================

        socket.onerror = function (error) {

            console.error(
                "❌ EMR WebSocket error:",
                error
            );

        };
    }


    // ========================================================
    // HEARTBEAT
    // ========================================================

    function startHeartbeat() {

        stopHeartbeat();

        heartbeatTimer = setInterval(
            function () {

                if (
                    socket &&
                    socket.readyState === WebSocket.OPEN
                ) {

                    socket.send(
                        JSON.stringify({
                            type: "ping"
                        })
                    );

                }

            },
            HEARTBEAT_INTERVAL
        );
    }


    function stopHeartbeat() {

        if (heartbeatTimer) {

            clearInterval(
                heartbeatTimer
            );

            heartbeatTimer = null;
        }
    }


    // ========================================================
    // RECONNECT
    // ========================================================

    function scheduleReconnect() {

        if (manuallyClosed) {
            return;
        }

        if (reconnectTimer) {
            return;
        }

        reconnectTimer = setTimeout(
            function () {

                reconnectTimer = null;

                connectWebSocket();

            },
            RECONNECT_DELAY
        );
    }


    // ========================================================
    // PUBLIC API
    // ========================================================

    window.EMRWebSocket = {

        connect: connectWebSocket,

        disconnect: function () {

            manuallyClosed = true;

            stopHeartbeat();

            if (socket) {
                socket.close();
            }

        },

        send: function (data) {

            if (
                socket &&
                socket.readyState === WebSocket.OPEN
            ) {

                socket.send(
                    JSON.stringify(data)
                );

            }

        },

        isConnected: function () {

            return (
                socket &&
                socket.readyState === WebSocket.OPEN
            );

        }

    };


    // ========================================================
    // AUTO START
    // ========================================================

    if (
        document.readyState === "loading"
    ) {

        document.addEventListener(
            "DOMContentLoaded",
            connectWebSocket
        );

    }

    else {

        connectWebSocket();

    }

})();