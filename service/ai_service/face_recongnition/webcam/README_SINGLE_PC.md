# 单机人脸追踪与优质截图分支

本目录的 `detect_yolo.py` 将本地 `V` 串口追踪状态机与 GitHub 现有
`detect_yolo.py` 的优质截图流程结合。对应固件是 `face_tracking_servo.ino`。
这是单机摄像头接 Arduino Uno 的版本；GitHub 的 `main` 分支和 PC2 的 TCP/UDP
服务仍使用原有程序。本分支不应直接合并到 PC2 服务。

## 实际行为

1. 人脸候选出现后，舵机暂停扫描，连续三个不同的采集帧确认。
2. 锁定后只追踪人脸；人脸进入画面短边 15% 半径的圆形区域后停止常规调整。
3. 只有脸在中心区域、没有视场边缘问题、画质合格、稳定两秒，才截图。
4. 稳定期间比较脸部裁剪图的清晰度，保存最清晰的**原始整帧**。先写
   `.writing`，再改名为 `.jpg`，避免识别服务读取未写完的图片。
5. 同一次停留只拍一张；脸消失 1.5 秒后重新允许拍摄，仍保留 3 秒冷却。
6. 多个人进入画面时继续维持原追踪目标，但暂停截图。

这个筛选有助于减少模糊、小脸和连续重复照片进入后续 InsightFace 流程。
`detect_yolo.py` 本身仍使用 Haar 检测脸，不执行身份比对，也无法保证提升
实际身份识别率。若身份判错，仍需检查 InsightFace 模型、登记照及阈值。

## Linux 单机运行

在 `webcam/` 目录准备 `yolo11n.pt`，安装依赖：

```bash
python3 -m venv .venv
.venv/bin/pip install ultralytics opencv-python pyserial
.venv/bin/python detect_yolo.py --camera 0 --serial-port /dev/ttyACM0
```

Windows 示例：`python detect_yolo.py --camera 0 --serial-port COM3`。
`--out` 可指定截图目录；在仓库 `webcam/` 运行时默认存到
`service/ai_service/face_recongnition/data/captures/`，供现有识别服务读取。
`--debug` 显示清晰度数值；`--require-eyes` 要求检测到两只眼睛，戴眼镜时
可能漏判，默认关闭。按 `q` 退出，按 `o` 发送单次开门脉冲。

Arduino Uno 烧录同目录的 `face_tracking_servo.ino`。默认 PAN D9、TILT D10、
DOOR D11，波特率 115200。实体舵机的转动方向和限位需现场验证。

## 代码检查

```bash
python -m py_compile detect_yolo.py
python -m unittest -v test_tracking test_detect_yolo
```

串口保持 `V,body,face,dx,dy,doorPulse\n`，所有字段为整数。没有新增网络
协议或坐标字段。由于 `V` 协议没有帧 ID，Arduino 无法独立识别被其他发送端
重放的旧包；本 Python 进程只对新拍到的帧发送一次，不重复发送旧结果。
