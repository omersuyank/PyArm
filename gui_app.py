import sys
import time
from typing import Optional

from PyQt6 import QtWidgets, QtCore, QtGui


class SerialWorker(QtCore.QThread):
    line_received = QtCore.pyqtSignal(str)
    connected = QtCore.pyqtSignal(str)
    disconnected = QtCore.pyqtSignal()

    def __init__(self, baud: int = 9600, parent=None):
        super().__init__(parent)
        self._baud = baud
        self._ser = None
        self._stop = False
        self._port_requested: Optional[str] = None

    def request_port(self, port: Optional[str]):
        self._port_requested = port

    def connect_to_port(self, port: str):
        # Mevcut baglantiyi kapat ve istenen porta gec
        self._port_requested = port
        self._safe_close()

    def run(self):
        while not self._stop:
            # Bagli degilse port ara/denele
            if self._ser is None:
                port = self._port_requested
                if port:
                    self._try_connect(port)
                    # Baglanti denemesinden sonra sifirla; tekrar baglanmak icin manuel tetiklenecek
                    self._port_requested = None
                else:
                    time.sleep(0.1)
                    continue

            # Bagli iken oku
            try:
                waiting = getattr(self._ser, 'in_waiting', 0)
                data = self._ser.read(waiting or 1)
                if data:
                    try:
                        text = data.decode(errors='ignore')
                    except Exception:
                        text = str(data)
                    self.line_received.emit(text)
                time.sleep(0.01)
            except Exception:
                self._safe_close()
                self.disconnected.emit()
                time.sleep(0.5)

    def stop(self):
        self._stop = True
        self._safe_close()
        self.wait(1000)

    def send_char(self, ch: str):
        if self._ser is None:
            return
        try:
            self._ser.write(ch.encode('utf-8', errors='ignore'))
        except Exception:
            self._safe_close()
            self.disconnected.emit()

    def _auto_detect_port(self) -> Optional[str]:
        # Otomatik baglanma artik kapatildi; bu fonksiyon kullanilmiyor.
        return None

    def _try_connect(self, port: str):
        try:
            import serial  # type: ignore
            self._ser = serial.Serial(port, baudrate=self._baud, timeout=0.1)
            time.sleep(2.0)  # Arduino reset bekleme
            self.connected.emit(port)
        except Exception:
            self._ser = None

    def _safe_close(self):
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None


class PlaybackWorker(QtCore.QThread):
    finished_ok = QtCore.pyqtSignal()
    stopped = QtCore.pyqtSignal()

    def __init__(self, events: list[tuple[int, str]], sender, loop: bool = False, parent=None):
        super().__init__(parent)
        self.events = sorted(events, key=lambda e: e[0])
        self.sender = sender  # callable: send_char
        self.loop = loop
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self.events:
            self.finished_ok.emit()
            return
        while not self._stop:
            start = int(time.time() * 1000)
            for t_rel, ch in self.events:
                if self._stop:
                    self.stopped.emit()
                    return
                now = int(time.time() * 1000)
                wait_ms = max(0, t_rel - (now - start))
                QtCore.QThread.msleep(wait_ms)
                try:
                    self.sender(ch)
                except Exception:
                    pass
            if not self.loop:
                break
        self.finished_ok.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Robot Kol - Seri Kontrol')
        self.resize(780, 520)

        # Operasyon kaydi ve geri alma icin durum
        self.active_motor: Optional[int] = None
        self.segment_start_ms = {1: None, 2: None, 3: None, 4: None}  # type: ignore
        self.segment_dir = {1: None, 2: None, 3: None, 4: None}       # 1=d, 2=a  # type: ignore
        self.reverse_actions: list[tuple[int, int, int]] = []  # (motor, inverse_dir, duration_ms)
        self.servo_angle_local: int = 0  # 0..180, baslangic 0
        self.ops_file = 'operations.txt'

        # Yerel (PC) kayit/oynatma
        self.local_events: list[tuple[int, str]] = []  # (t_ms_since_start, command_char)
        self.local_rec_start_ms: Optional[int] = None
        self.is_local_recording: bool = False
        self.playback_thread: Optional[PlaybackWorker] = None

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Port ve baglanti alani
        top_bar = QtWidgets.QHBoxLayout()
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton('Portları Yenile')
        self.connect_btn = QtWidgets.QPushButton('Bağlan')
        self.status_lbl = QtWidgets.QLabel('Durum: Bağlı değil')
        top_bar.addWidget(self.port_combo)
        top_bar.addWidget(self.refresh_btn)
        top_bar.addWidget(self.connect_btn)
        top_bar.addWidget(self.status_lbl)
        layout.addLayout(top_bar)

        # Komut butonlari
        grid = QtWidgets.QGridLayout()
        row = 0
        # Motor secim 1-5, adlar: 1=Kafa, 2=Sağ Sol, 3=Boyun, 4=Gövde, 5=Gripper
        motor_labels = ['Kafa', 'Sağ Sol', 'Boyun', 'Gövde', 'Gripper']
        for idx, label in enumerate(motor_labels, start=1):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(f"Motor {idx} - {label}")
            btn.clicked.connect(lambda _=False, ch=str(idx): self.select_motor(int(ch)))
            grid.addWidget(btn, row, idx - 1)
        row += 1

        # Step ileri/geri/dur
        btn_d = QtWidgets.QPushButton('d (ileri/+60)')
        btn_a = QtWidgets.QPushButton('a (geri/-60)')
        btn_w = QtWidgets.QPushButton('w (dur/bekle)')
        btn_d.clicked.connect(lambda: self.handle_motion('d'))
        btn_a.clicked.connect(lambda: self.handle_motion('a'))
        btn_w.clicked.connect(lambda: self.handle_motion('w'))
        grid.addWidget(btn_d, row, 0)
        grid.addWidget(btn_a, row, 1)
        grid.addWidget(btn_w, row, 2)
        row += 1

        # Servo ince adimlar ve 0
        btn_lb = QtWidgets.QPushButton('[ (+15)')
        btn_rb = QtWidgets.QPushButton('] (-15)')
        btn_c = QtWidgets.QPushButton('c (0°)')
        btn_lb.clicked.connect(lambda: self.handle_servo('['))
        btn_rb.clicked.connect(lambda: self.handle_servo(']'))
        btn_c.clicked.connect(lambda: self.handle_servo('c'))
        grid.addWidget(btn_lb, row, 0)
        grid.addWidget(btn_rb, row, 1)
        grid.addWidget(btn_c, row, 2)
        row += 1

        # Kayıt/Oynatma - ayrik butonlar ve durum etiketleri
        rec_start_btn = QtWidgets.QPushButton('Kayıt Başlat')
        rec_stop_btn  = QtWidgets.QPushButton('Kayıt Durdur')
        play_start_btn = QtWidgets.QPushButton('Oynat Başlat')
        play_stop_btn  = QtWidgets.QPushButton('Oynat Durdur')
        loop_toggle_btn = QtWidgets.QPushButton('Loop Aç/Kapa')
        info_btn = QtWidgets.QPushButton('Bilgi (V)')

        rec_start_btn.clicked.connect(lambda: self._rec_play_action('R'))
        rec_stop_btn.clicked.connect(lambda: self._rec_play_action('T'))
        play_start_btn.clicked.connect(lambda: self._rec_play_action('P'))
        play_stop_btn.clicked.connect(lambda: self._rec_play_action('S'))
        loop_toggle_btn.clicked.connect(lambda: self._rec_play_action('L'))
        info_btn.clicked.connect(lambda: self._rec_play_action('V'))

        grid.addWidget(rec_start_btn, row, 0)
        grid.addWidget(rec_stop_btn,  row, 1)
        grid.addWidget(play_start_btn, row, 2)
        grid.addWidget(play_stop_btn,  row, 3)
        grid.addWidget(loop_toggle_btn, row, 4)
        grid.addWidget(info_btn, row, 5)
        row += 1

        status_bar2 = QtWidgets.QHBoxLayout()
        self.lbl_rec = QtWidgets.QLabel('Kayıt: Kapalı')
        self.lbl_play = QtWidgets.QLabel('Oynatma: Kapalı')
        self.lbl_loop = QtWidgets.QLabel('Loop: Kapalı')
        status_bar2.addWidget(self.lbl_rec)
        status_bar2.addWidget(self.lbl_play)
        status_bar2.addWidget(self.lbl_loop)
        layout.addLayout(status_bar2)

        # Home'a don ve kaydi sifirla
        btn_home = QtWidgets.QPushButton('Home\'a Dön (Geri Al)')
        btn_reset = QtWidgets.QPushButton('Kaydı Sıfırla')
        btn_home.clicked.connect(self.return_to_home)
        btn_reset.clicked.connect(self.reset_operations)
        grid.addWidget(btn_home, row, 0)
        grid.addWidget(btn_reset, row, 1)
        layout.addLayout(grid)

        # Log penceresi
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        # Serial worker
        self.worker = SerialWorker(baud=9600)
        self.worker.line_received.connect(self.on_serial_line)
        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.start()

        # UI baglantilari
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.manual_connect)

        self.refresh_ports()
        # Otomatik baglanma kaldirildi: kullanici secip baglanacak

    def closeEvent(self, event):
        try:
            self.worker.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def refresh_ports(self):
        self.port_combo.clear()
        try:
            from serial.tools import list_ports  # type: ignore
            ports = list_ports.comports()
            target_index = -1
            idx_ui = 0
            for p in ports:
                desc = (p.description or '').lower()
                is_bt = ('bluetooth' in desc) or ('standard serial over bluetooth' in desc)
                if is_bt:
                    continue
                self.port_combo.addItem(f"{p.device} - {p.description}", p.device)
                if str(p.device).upper() == 'COM6':
                    target_index = idx_ui
                idx_ui += 1
            if target_index >= 0:
                self.port_combo.setCurrentIndex(target_index)
        except Exception:
            pass

    def manual_connect(self):
        device = self.port_combo.currentData()
        if device:
            self.status_lbl.setText(f"Durum: Bağlanıyor ({device})...")
            self.worker.connect_to_port(str(device))

    def on_serial_line(self, text: str):
        # Basit ve guvenli: dogrudan ekle
        self.log.appendPlainText(text)

    def on_connected(self, port: str):
        self.status_lbl.setText(f'Durum: Bağlı ({port})')
        self.log.appendPlainText(f"[INFO] Bağlandı: {port}\n")

    def on_disconnected(self):
        self.status_lbl.setText('Durum: Bağlı değil')
        self.log.appendPlainText("[WARN] Bağlantı koptu, yeniden denenecek...\n")

    def send(self, ch: str):
        self.worker.send_char(ch)
        # Genel log
        self._append_operation(f"SEND {ch}")
        # Yerel kayit aktifse komutu zamanla birlikte ekle
        if self.is_local_recording:
            now = int(time.time() * 1000)
            if self.local_rec_start_ms is None:
                self.local_rec_start_ms = now
            t_rel = now - int(self.local_rec_start_ms)
            self.local_events.append((t_rel, ch))

    def _rec_play_action(self, code: str):
        # UI uzerinden R/T/P/S/L/V gonderimi yerine yerel kayit/oynatma uygula
        if code == 'R':
            self._local_record_start()
            self.lbl_rec.setText('Kayıt: Açık')
            return
        if code == 'T':
            self._local_record_stop()
            self.lbl_rec.setText('Kayıt: Kapalı')
            return
        if code == 'P':
            self._local_play_start()
            self.lbl_play.setText('Oynatma: Açık')
            return
        if code == 'S':
            self._local_play_stop()
            self.lbl_play.setText('Oynatma: Kapalı')
            return
        if code == 'L':
            # basit toggle: mevcut yazidan anla
            is_on = 'Açık' in self.lbl_loop.text()
            self.lbl_loop.setText('Loop: Kapalı' if is_on else 'Loop: Açık')
            return
        if code == 'V':
            # Arduino bilgisini gondermek istersen yine yolla
            self.send('V')
            return

    # --- Local record/play implementation ---
    def _local_record_start(self):
        self.local_events.clear()
        self.local_rec_start_ms = None
        self.is_local_recording = True
        self._append_operation('LOCAL REC START')

    def _local_record_stop(self):
        self.is_local_recording = False
        self._append_operation(f'LOCAL REC STOP (events={len(self.local_events)})')

    def _local_play_start(self):
        # Zaten calisiyorsa once durdur
        self._local_play_stop()
        if not self.local_events:
            self._append_operation('LOCAL PLAY: no events')
            return
        self.playback_thread = PlaybackWorker(self.local_events, self.worker.send_char, loop=('Açık' in self.lbl_loop.text()))
        self.playback_thread.finished_ok.connect(lambda: self._append_operation('LOCAL PLAY DONE'))
        self.playback_thread.stopped.connect(lambda: self._append_operation('LOCAL PLAY STOPPED'))
        self.playback_thread.start()
        self._append_operation('LOCAL PLAY START')

    def _local_play_stop(self):
        if self.playback_thread and self.playback_thread.isRunning():
            self.playback_thread.stop()
            self.playback_thread.wait(1000)
        self.playback_thread = None


    # --- Operation logging helpers ---
    def _append_operation(self, line: str):
        ts = QtCore.QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss.zzz')
        entry = f"[{ts}] {line}\n"
        try:
            with open(self.ops_file, 'a', encoding='utf-8') as f:
                f.write(entry)
        except Exception:
            pass
        # Ayrica UI log
        self.log.appendPlainText(entry.rstrip('\n'))

    def reset_operations(self):
        self.reverse_actions.clear()
        for k in self.segment_start_ms.keys():
            self.segment_start_ms[k] = None
            self.segment_dir[k] = None
        self.servo_angle_local = 0
        try:
            open(self.ops_file, 'w', encoding='utf-8').close()
        except Exception:
            pass
        self._append_operation('RESET')

    # --- Motor/Servo handlers with logging & reverse ---
    def select_motor(self, motor: int):
        self.active_motor = motor
        self.send(str(motor))
        self._append_operation(f"SELECT M{motor}")

    def handle_motion(self, code: str):
        # Only for steppers 1..4
        if not self.active_motor or self.active_motor not in (1, 2, 3, 4):
            return
        now = int(time.time() * 1000)
        motor = self.active_motor

        if code in ('d', 'a'):
            # Kapanmamis segment varsa kapat
            if self.segment_start_ms[motor] is not None and self.segment_dir[motor] is not None:
                duration = now - int(self.segment_start_ms[motor])
                inv_dir = 2 if self.segment_dir[motor] == 1 else 1
                self.reverse_actions.append((motor, inv_dir, max(0, duration)))
                self._append_operation(f"M{motor} STOP duration={duration}ms")
            # Yeni segment baslat
            self.segment_start_ms[motor] = now
            self.segment_dir[motor] = 1 if code == 'd' else 2
            self._append_operation(f"M{motor} START dir={'ILERI' if code=='d' else 'GERI'}")
            self.send(code)
        elif code == 'w':
            if self.segment_start_ms[motor] is not None and self.segment_dir[motor] is not None:
                duration = now - int(self.segment_start_ms[motor])
                inv_dir = 2 if self.segment_dir[motor] == 1 else 1
                self.reverse_actions.append((motor, inv_dir, max(0, duration)))
                self._append_operation(f"M{motor} STOP duration={duration}ms")
            self.segment_start_ms[motor] = None
            self.segment_dir[motor] = None
            self.send('w')

    def handle_servo(self, code: str):
        # Only when motor 5 selected
        if self.active_motor != 5:
            return
        if code == 'c':
            self.servo_angle_local = 0
        elif code == '[':
            self.servo_angle_local = min(180, self.servo_angle_local + 15)
        elif code == ']':
            self.servo_angle_local = max(0, self.servo_angle_local - 15)
        elif code == 'd':
            self.servo_angle_local = min(180, self.servo_angle_local + 60)
        elif code == 'a':
            self.servo_angle_local = max(0, self.servo_angle_local - 60)
        self._append_operation(f"SERVO angle~{self.servo_angle_local}")
        self.send(code)

    def return_to_home(self):
        # Servo -> 0 derece
        if self.active_motor != 5:
            self.select_motor(5)
        self.send('c')  # absolute 0°
        self._append_operation('SERVO -> 0')

        # Stepper hareketlerini tersinden oyna
        for motor, inv_dir, duration in reversed(self.reverse_actions):
            if self.active_motor != motor:
                self.select_motor(motor)
            self.send('d' if inv_dir == 1 else 'a')
            QtWidgets.QApplication.processEvents()
            QtCore.QThread.msleep(max(0, duration))
            self.send('w')
        self._append_operation('HOME DONE')
        # Temizle
        self.reverse_actions.clear()


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()


