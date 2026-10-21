"""Safe adapter for the copied YOLO-pose LSTM fall checkpoint."""

from __future__ import annotations

from pathlib import Path

import numpy as np


class YoloPoseLstmPredictor:
    """Return the bundled model's Fall-class softmax probability for 40x3 input."""

    WINDOW_SIZE = 40
    FEATURE_COUNT = 3

    def __init__(self, model_path: str | Path, *, device: str | None = None) -> None:
        try:
            import torch
            import torch.nn as nn
        except ImportError as exc:
            raise RuntimeError("PyTorch is required for lstm_fall_model.pth") from exc

        self.torch = torch
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"LSTM checkpoint not found: {self.model_path}")

        class LSTMClassifier(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.lstm = nn.LSTM(3, 64, batch_first=True)
                self.dropout = nn.Dropout(0.3)
                self.fc = nn.Linear(64, 2)

            def forward(self, x):
                _, (hidden, _) = self.lstm(x)
                return self.fc(self.dropout(hidden[-1]))

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.model = LSTMClassifier().to(self.device)
        self.model.load_state_dict(torch.load(self.model_path, map_location=self.device, weights_only=True))
        self.model.eval()

    def __call__(self, window: np.ndarray) -> float:
        features = np.asarray(window, dtype=np.float32)
        expected = (self.WINDOW_SIZE, self.FEATURE_COUNT)
        if features.shape != expected:
            raise ValueError(f"expected LSTM input window {expected}, got {features.shape}")
        if not np.isfinite(features).all():
            raise ValueError("LSTM input contains non-finite values")
        input_tensor = self.torch.from_numpy(features).unsqueeze(0).to(self.device)
        with self.torch.no_grad():
            probabilities = self.torch.softmax(self.model(input_tensor), dim=1)[0]
        return float(probabilities[1].detach().cpu())
