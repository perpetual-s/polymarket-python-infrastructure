"""
Unified WebSocket Manager for coordinating CLOB and RTDS connections.

Provides single interface for lifecycle management, health monitoring,
and graceful degradation across both WebSocket types.

v3.3: Phase 3.1 implementation.
"""

import asyncio
import logging
from typing import Optional, Dict, Any

from .websocket import WebSocketClient
from .real_time_data import RealTimeDataClient

logger = logging.getLogger(__name__)


class UnifiedWebSocketManager:
    """
    Unified manager for both CLOB and RTDS WebSocket connections.

    Features:
    - Coordinated lifecycle (start/stop both atomically)
    - Unified health monitoring
    - Graceful degradation (continue if one fails)
    - Single interface for operations
    """

    def __init__(
        self,
        clob_ws: WebSocketClient,
        rtds: RealTimeDataClient
    ):
        """
        Initialize unified manager.

        Args:
            clob_ws: CLOB WebSocket client instance
            rtds: RTDS client instance
        """
        self.clob = clob_ws
        self.rtds = rtds
        self._running = False

        logger.info("UnifiedWebSocketManager initialized")

    def start_all(self, event_loop: Optional[asyncio.AbstractEventLoop] = None) -> Dict[str, bool]:
        """
        Start both WebSocket connections atomically.

        Args:
            event_loop: Optional event loop for CLOB consumer task

        Returns:
            dict: Status of each connection {"clob": True/False, "rtds": True/False}
        """
        if self._running:
            logger.warning("WebSocket manager already running")
            return {"clob": True, "rtds": True}

        self._running = True
        status = {"clob": False, "rtds": False}

        # Start CLOB WebSocket
        try:
            self.clob.connect(event_loop=event_loop)
            status["clob"] = True
            logger.info("CLOB WebSocket started")
        except Exception as e:
            logger.error(f"Failed to start CLOB WebSocket: {e}", exc_info=True)

        # Start RTDS
        try:
            self.rtds.connect()
            status["rtds"] = True
            logger.info("RTDS client started")
        except Exception as e:
            logger.error(f"Failed to start RTDS: {e}", exc_info=True)

        # Log overall status
        if status["clob"] and status["rtds"]:
            logger.info("All WebSocket connections started successfully")
        elif status["clob"] or status["rtds"]:
            logger.warning(f"Partial WebSocket startup: CLOB={status['clob']}, RTDS={status['rtds']}")
        else:
            logger.error("Failed to start any WebSocket connections")
            self._running = False

        return status

    def stop_all(self) -> Dict[str, bool]:
        """
        Stop both WebSocket connections atomically.

        Returns:
            dict: Status of each disconnection {"clob": True/False, "rtds": True/False}
        """
        if not self._running:
            logger.warning("WebSocket manager not running")
            return {"clob": False, "rtds": False}

        self._running = False
        status = {"clob": False, "rtds": False}

        # Stop CLOB WebSocket
        try:
            self.clob.disconnect()
            status["clob"] = True
            logger.info("CLOB WebSocket stopped")
        except Exception as e:
            logger.error(f"Error stopping CLOB WebSocket: {e}", exc_info=True)

        # Stop RTDS
        try:
            self.rtds.disconnect()
            status["rtds"] = True
            logger.info("RTDS client stopped")
        except Exception as e:
            logger.error(f"Error stopping RTDS: {e}", exc_info=True)

        return status

    def health_status(self) -> Dict[str, Any]:
        """
        Get combined health status of both connections.

        Returns:
            dict: Health status with detailed stats for each connection
        """
        health = {
            "manager_running": self._running,
            "clob": {},
            "rtds": {},
        }

        # Get CLOB WebSocket health
        try:
            health["clob"]["health"] = self.clob.health_check()
            health["clob"]["stats"] = self.clob.stats()
        except Exception as e:
            logger.error(f"Error getting CLOB health: {e}")
            health["clob"]["error"] = str(e)

        # Get RTDS health
        try:
            # RTDS doesn't have health_check, just stats
            if hasattr(self.rtds, 'stats'):
                health["rtds"]["stats"] = self.rtds.stats()
            else:
                health["rtds"]["stats"] = {"status": "unknown"}
        except Exception as e:
            logger.error(f"Error getting RTDS health: {e}")
            health["rtds"]["error"] = str(e)

        # Overall health assessment
        clob_healthy = health["clob"].get("health", {}).get("status") == "healthy"
        rtds_connected = health["rtds"].get("stats", {}).get("status") == "connected"

        if clob_healthy and rtds_connected:
            health["overall_status"] = "healthy"
        elif clob_healthy or rtds_connected:
            health["overall_status"] = "degraded"
        else:
            health["overall_status"] = "unhealthy"

        return health

    def is_running(self) -> bool:
        """Check if manager is running."""
        return self._running

    def get_clob_stats(self) -> Dict[str, Any]:
        """Get CLOB WebSocket statistics."""
        return self.clob.stats()

    def get_rtds_stats(self) -> Dict[str, Any]:
        """Get RTDS statistics."""
        if hasattr(self.rtds, 'stats'):
            return self.rtds.stats()
        return {"status": "unknown"}

    # Context manager support
    def __enter__(self):
        """Context manager entry."""
        self.start_all()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_all()
