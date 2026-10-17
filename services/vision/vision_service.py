"""
GuardianBox Vision Service

Provides:
- MJPEG stream with skeleton overlay
- Snapshot endpoint
- Video source switching (camera, RTSP, video files)
- YOLO pose detection with fall/collapse triggers
- Posts pose triggers to orchestrator

Works on CPU with optional GPU acceleration.
Designed for ARM64 (DGX Spark) and x86_64.
"""

import os
import sys
import asyncio
import time
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from threading import Thread, Lock
from collections import deque
from enum import Enum

import cv2
import numpy as np
import httpx
from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="GuardianBox Vision Service")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "/samples/clip2.mp4")
SAMPLES_DIR = os.getenv("SAMPLES_DIR", "/samples")
FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "640"))
FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "480"))
TARGET_FPS = int(os.getenv("TARGET_FPS", "15"))
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"
POSE_MODEL = os.getenv("POSE_MODEL", "yolo11n-pose")

POSE_CONNECTIONS = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Head
    (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),  # Arms
    (5, 11), (6, 12), (11, 12),  # Torso
    (11, 13), (12, 14), (13, 15), (14, 16)  # Legs
]

KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle"
]


class TriggerType(str, Enum):
    FAST_FALL = "fast_fall"
    SLOW_COLLAPSE = "slow_collapse"
    FLOOR_STILLNESS = "floor_stillness"


class PoseAnalyzer:
    """Analyzes pose keypoints to detect falls and collapses."""
    
    def __init__(self):
        self.history: deque = deque(maxlen=90)  # ~3 seconds at 30fps
        self.last_trigger_time = 0
        self.trigger_cooldown = 10.0  # seconds
        self.standing_hip_height: Optional[float] = None
        self.floor_still_start: Optional[float] = None
        
    def analyze(self, keypoints: np.ndarray, timestamp: float) -> Optional[Dict[str, Any]]:
        """
        Analyze keypoints and detect fall/collapse events.
        
        Args:
            keypoints: (17, 3) array of [x, y, confidence] for each keypoint
            timestamp: Current frame timestamp
            
        Returns:
            Trigger data dict if event detected, None otherwise
        """
        if keypoints is None or len(keypoints) < 17:
            return None
            
        self.history.append((timestamp, keypoints))
        
        if timestamp - self.last_trigger_time < self.trigger_cooldown:
            return None
        
        left_shoulder = keypoints[5]
        right_shoulder = keypoints[6]
        left_hip = keypoints[11]
        right_hip = keypoints[12]
        nose = keypoints[0]
        
        shoulder_conf = min(left_shoulder[2], right_shoulder[2])
        hip_conf = min(left_hip[2], right_hip[2])
        
        if shoulder_conf < 0.3 or hip_conf < 0.3:
            return None
            
        shoulder_mid = (left_shoulder[:2] + right_shoulder[:2]) / 2
        hip_mid = (left_hip[:2] + right_hip[:2]) / 2
        
        torso_vec = shoulder_mid - hip_mid
        torso_angle = np.degrees(np.arctan2(abs(torso_vec[0]), abs(torso_vec[1])))
        
        hip_height = 1.0 - (hip_mid[1] / FRAME_HEIGHT)
        head_height = 1.0 - (nose[1] / FRAME_HEIGHT) if nose[2] > 0.3 else hip_height + 0.2
        
        if self.standing_hip_height is None and torso_angle < 30 and hip_height > 0.4:
            self.standing_hip_height = hip_height
        
        motion_energy = self._calc_motion_energy()
        stillness_seconds = self._calc_stillness(timestamp, motion_energy)
        rapid_descent = self._detect_rapid_descent(timestamp, hip_mid[1])
        
        prayer_cycles = self._detect_prayer_cycles()
        
        trigger = None
        
        if rapid_descent and torso_angle > 50:
            trigger = TriggerType.FAST_FALL
        elif torso_angle > 60 and stillness_seconds > 3 and hip_height < 0.3:
            trigger = TriggerType.SLOW_COLLAPSE
        elif hip_height < 0.25 and stillness_seconds > 5:
            trigger = TriggerType.FLOOR_STILLNESS
            
        if trigger:
            self.last_trigger_time = timestamp
            
            head_height_ratio = head_height
            hip_descent_ratio = 0.0
            if self.standing_hip_height:
                hip_descent_ratio = max(0, self.standing_hip_height - hip_height) / self.standing_hip_height
            
            return {
                "trigger_type": trigger.value,
                "torso_angle": float(torso_angle),
                "head_height_ratio": float(head_height_ratio),
                "hip_descent_ratio": float(hip_descent_ratio),
                "stillness_seconds": float(stillness_seconds),
                "motion_energy": float(motion_energy),
                "prayer_cycles": int(prayer_cycles),
                "rapid_descent": rapid_descent,
                "zone": "floor" if hip_height < 0.3 else "standing",
            }
            
        return None
    
    def _calc_motion_energy(self) -> float:
        """Calculate motion energy from recent keypoint history."""
        if len(self.history) < 2:
            return 0.0
            
        recent = list(self.history)[-10:]
        if len(recent) < 2:
            return 0.0
            
        total_motion = 0.0
        for i in range(1, len(recent)):
            prev_kp = recent[i-1][1]
            curr_kp = recent[i][1]
            
            for j in range(17):
                if prev_kp[j][2] > 0.3 and curr_kp[j][2] > 0.3:
                    dx = curr_kp[j][0] - prev_kp[j][0]
                    dy = curr_kp[j][1] - prev_kp[j][1]
                    total_motion += np.sqrt(dx*dx + dy*dy)
                    
        return total_motion / (len(recent) * FRAME_WIDTH)
    
    def _calc_stillness(self, timestamp: float, motion_energy: float) -> float:
        """Calculate how long the person has been still."""
        if motion_energy < 0.01:
            if self.floor_still_start is None:
                self.floor_still_start = timestamp
            return timestamp - self.floor_still_start
        else:
            self.floor_still_start = None
            return 0.0
    
    def _detect_rapid_descent(self, timestamp: float, current_hip_y: float) -> bool:
        """Detect if hip dropped rapidly (fall signature)."""
        if len(self.history) < 15:
            return False
            
        one_sec_ago = [(t, kp) for t, kp in self.history if timestamp - t > 0.5 and timestamp - t < 1.5]
        if not one_sec_ago:
            return False
            
        past_hip_y = (one_sec_ago[0][1][11][1] + one_sec_ago[0][1][12][1]) / 2
        descent = current_hip_y - past_hip_y
        
        return descent > FRAME_HEIGHT * 0.3
    
    def _detect_prayer_cycles(self) -> int:
        """Detect number of prayer cycles (prostration-standing transitions)."""
        if len(self.history) < 60:
            return 0
            
        low_count = 0
        high_count = 0
        prev_low = False
        cycles = 0
        
        for _, kp in self.history:
            hip_height = 1.0 - ((kp[11][1] + kp[12][1]) / 2 / FRAME_HEIGHT)
            is_low = hip_height < 0.3
            
            if is_low and not prev_low:
                low_count += 1
            elif not is_low and prev_low:
                high_count += 1
                cycles = min(low_count, high_count)
            prev_low = is_low
            
        return cycles


class VideoCapture:
    """Thread-safe video capture with looping support."""
    
    def __init__(self):
        self.cap: Optional[cv2.VideoCapture] = None
        self.source: str = ""
        self.frame: Optional[np.ndarray] = None
        self.frame_lock = Lock()
        self.running = False
        self.thread: Optional[Thread] = None
        self.fps = TARGET_FPS
        self.frame_count = 0
        self.keypoints: Optional[np.ndarray] = None
        self.pose_model = None
        self.pose_analyzer = PoseAnalyzer()
        self.http_client: Optional[httpx.AsyncClient] = None
        self.trigger_queue: deque = deque(maxlen=10)
        
    def start(self, source: str):
        """Start capturing from source."""
        self.stop()
        self.source = source
        self.running = True
        self.thread = Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        logger.info(f"Started video capture from: {source}")
        
    def stop(self):
        """Stop capturing."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
            self.cap = None
            
    def _capture_loop(self):
        """Main capture loop (runs in separate thread)."""
        self._load_pose_model()
        
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                self._open_source()
                if self.cap is None:
                    time.sleep(1)
                    continue
                    
            ret, frame = self.cap.read()
            
            if not ret:
                if self._is_video_file():
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                else:
                    time.sleep(0.1)
                    continue
            
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            
            keypoints = self._run_pose_detection(frame)
            
            if keypoints is not None:
                frame = self._draw_skeleton(frame, keypoints)
                trigger = self.pose_analyzer.analyze(keypoints, time.time())
                if trigger:
                    self.trigger_queue.append(trigger)
                    
            with self.frame_lock:
                self.frame = frame
                self.keypoints = keypoints
                self.frame_count += 1
                
            time.sleep(1.0 / self.fps)
            
    def _open_source(self):
        """Open the video source."""
        source = self.source
        
        if source.startswith("/dev/video"):
            self.cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        elif source.startswith("rtsp://"):
            self.cap = cv2.VideoCapture(source)
        elif os.path.isfile(source):
            self.cap = cv2.VideoCapture(source)
            if self.cap.isOpened():
                video_fps = self.cap.get(cv2.CAP_PROP_FPS)
                if video_fps > 0:
                    self.fps = min(video_fps, TARGET_FPS)
        else:
            logger.warning(f"Source not found: {source}")
            self.cap = None
            return
            
        if self.cap and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            logger.info(f"Opened video source: {source}")
        else:
            logger.warning(f"Failed to open video source: {source}")
            self.cap = None
            
    def _is_video_file(self) -> bool:
        """Check if source is a video file (should loop)."""
        return os.path.isfile(self.source)
    
    def _load_pose_model(self):
        """Load YOLO pose model if available."""
        try:
            from ultralytics import YOLO
            model_name = f"{POSE_MODEL}.pt"
            self.pose_model = YOLO(model_name)
            logger.info(f"Loaded YOLO pose model: {model_name}")
        except ImportError:
            logger.warning("Ultralytics not available, using simple pose estimation")
            self.pose_model = None
        except Exception as e:
            logger.warning(f"Failed to load YOLO model: {e}")
            self.pose_model = None
            
    def _run_pose_detection(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Run pose detection on frame."""
        if self.pose_model is not None:
            try:
                results = self.pose_model(frame, verbose=False)
                if results and len(results) > 0:
                    keypoints = results[0].keypoints
                    if keypoints is not None and len(keypoints.data) > 0:
                        kp_data = keypoints.data[0].cpu().numpy()
                        return kp_data
            except Exception as e:
                logger.debug(f"Pose detection error: {e}")
                
        return self._simple_pose_estimation(frame)
    
    def _simple_pose_estimation(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Simple fallback pose estimation using motion detection."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
            
        largest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest) < 1000:
            return None
            
        x, y, w, h = cv2.boundingRect(largest)
        
        keypoints = np.zeros((17, 3))
        
        cx, cy = x + w/2, y + h/2
        keypoints[0] = [cx, y + h*0.1, 0.8]  # nose
        keypoints[5] = [cx - w*0.2, y + h*0.25, 0.7]  # left_shoulder
        keypoints[6] = [cx + w*0.2, y + h*0.25, 0.7]  # right_shoulder
        keypoints[11] = [cx - w*0.1, y + h*0.55, 0.7]  # left_hip
        keypoints[12] = [cx + w*0.1, y + h*0.55, 0.7]  # right_hip
        keypoints[13] = [cx - w*0.15, y + h*0.75, 0.5]  # left_knee
        keypoints[14] = [cx + w*0.15, y + h*0.75, 0.5]  # right_knee
        keypoints[15] = [cx - w*0.15, y + h*0.95, 0.5]  # left_ankle
        keypoints[16] = [cx + w*0.15, y + h*0.95, 0.5]  # right_ankle
        
        return keypoints
    
    def _draw_skeleton(self, frame: np.ndarray, keypoints: np.ndarray) -> np.ndarray:
        """Draw skeleton overlay on frame."""
        frame = frame.copy()
        
        for i, j in POSE_CONNECTIONS:
            if keypoints[i][2] > 0.3 and keypoints[j][2] > 0.3:
                pt1 = (int(keypoints[i][0]), int(keypoints[i][1]))
                pt2 = (int(keypoints[j][0]), int(keypoints[j][1]))
                cv2.line(frame, pt1, pt2, (0, 255, 0), 2)
                
        for i, kp in enumerate(keypoints):
            if kp[2] > 0.3:
                pt = (int(kp[0]), int(kp[1]))
                color = (0, 255, 255) if i < 5 else (0, 255, 0)  # Yellow for head
                cv2.circle(frame, pt, 4, color, -1)
                
        cv2.putText(frame, f"Frame: {self.frame_count}", (10, 25),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return frame
    
    def get_frame(self) -> Optional[np.ndarray]:
        """Get current frame (thread-safe)."""
        with self.frame_lock:
            return self.frame.copy() if self.frame is not None else None
            
    def get_jpeg(self) -> Optional[bytes]:
        """Get current frame as JPEG bytes."""
        frame = self.get_frame()
        if frame is None:
            return None
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes()


video_capture = VideoCapture()
trigger_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def startup():
    global trigger_task
    video_capture.start(CAMERA_SOURCE)
    trigger_task = asyncio.create_task(trigger_sender_loop())
    logger.info(f"Vision service started (mock={MOCK_MODE}, source={CAMERA_SOURCE})")


@app.on_event("shutdown")
async def shutdown():
    global trigger_task
    video_capture.stop()
    if trigger_task:
        trigger_task.cancel()


async def trigger_sender_loop():
    """Background loop to send pose triggers to orchestrator."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                while video_capture.trigger_queue:
                    trigger = video_capture.trigger_queue.popleft()
                    try:
                        response = await client.post(
                            f"{ORCHESTRATOR_URL}/api/v1/demo/pose-trigger",
                            json=trigger
                        )
                        logger.info(f"Sent trigger {trigger['trigger_type']}: {response.status_code}")
                    except Exception as e:
                        logger.warning(f"Failed to send trigger: {e}")
            except Exception as e:
                logger.error(f"Trigger sender error: {e}")
            await asyncio.sleep(0.5)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "mock": MOCK_MODE,
        "source": video_capture.source,
        "frame_count": video_capture.frame_count,
        "pose_model": POSE_MODEL if video_capture.pose_model else "fallback",
    }


@app.get("/stream.mjpg")
async def mjpeg_stream():
    """MJPEG video stream with skeleton overlay."""
    def generate():
        while True:
            jpeg = video_capture.get_jpeg()
            if jpeg:
                yield b"--frame\r\n"
                yield b"Content-Type: image/jpeg\r\n\r\n"
                yield jpeg
                yield b"\r\n"
            time.sleep(1.0 / TARGET_FPS)
            
    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/snapshot.jpg")
async def snapshot():
    """Get latest frame as JPEG."""
    jpeg = video_capture.get_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=503, detail="No frame available")
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/latest.jpg")
async def latest_frame():
    """Alias for /snapshot.jpg for compatibility."""
    return await snapshot()


class SourceRequest(BaseModel):
    source: str


@app.post("/source")
async def set_source(req: SourceRequest):
    """Switch video source."""
    video_capture.start(req.source)
    return {"status": "ok", "source": req.source}


@app.get("/sources")
async def list_sources():
    """List available video sources (clips in samples/)."""
    sources = []
    
    samples_path = Path(SAMPLES_DIR)
    if samples_path.exists():
        for mp4 in sorted(samples_path.glob("*.mp4")):
            sources.append({
                "path": str(mp4),
                "name": mp4.stem,
                "type": "file",
            })
    
    for dev in Path("/dev").glob("video*"):
        sources.append({
            "path": str(dev),
            "name": dev.name,
            "type": "camera",
        })
        
    return {
        "sources": sources,
        "current": video_capture.source,
    }


@app.get("/pose")
async def get_pose():
    """Get current pose keypoints."""
    with video_capture.frame_lock:
        keypoints = video_capture.keypoints
        
    if keypoints is None:
        return {"keypoints": None, "detected": False}
        
    return {
        "keypoints": keypoints.tolist(),
        "detected": True,
        "names": KEYPOINT_NAMES,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
