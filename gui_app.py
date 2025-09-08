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


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Robot Kol - Seri Kontrol')
        self.resize(780, 520)

        # Operasyon kaydi ve geri alma icin durum
        self.active_motor: Optional[int] = None
        self.selected_motors: set[int] = set()  # Çoklu motor seçimi için
        self.segment_start_ms = {1: None, 2: None, 3: None, 4: None, 5: None}  # type: ignore
        self.segment_dir = {1: None, 2: None, 3: None, 4: None, 5: None}       # 1=d, 2=a  # type: ignore
        self.reverse_actions: list[tuple[int, int, int]] = []  # (motor, inverse_dir, duration_ms)
        self.servo_angle_local: int = 0  # 0..180, baslangic 0
        self.ops_file = 'operations.txt'

        # Arduino kayıt/oynatma sistemi kullanılıyor

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

        # Hız kontrolü (stepDelay µs)
        speed_bar = QtWidgets.QHBoxLayout()
        self.lbl_speed_title = QtWidgets.QLabel('Hız (µs):')
        self.slider_speed = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_speed.setMinimum(150)
        self.slider_speed.setMaximum(4000)
        self.slider_speed.setSingleStep(10)
        self.slider_speed.setPageStep(50)
        self.slider_speed.setValue(3000)
        self.lbl_speed_value = QtWidgets.QLabel('3000')
        speed_bar.addWidget(self.lbl_speed_title)
        speed_bar.addWidget(self.slider_speed)
        speed_bar.addWidget(self.lbl_speed_value)
        layout.addLayout(speed_bar)

        # Komut butonlari
        grid = QtWidgets.QGridLayout()
        row = 0
        
        # Motor seçimi için checkbox'lar ve butonlar
        motor_selection_layout = QtWidgets.QHBoxLayout()
        motor_labels = ['Kafa', 'Kafa Sağ Sol', 'Boyun', 'Gövde', 'SağSol', 'Gripper']
        self.motor_checkboxes = {}
        
        for idx, label in enumerate(motor_labels, start=1):
            checkbox = QtWidgets.QCheckBox(f"M{idx}: {label}")
            checkbox.setToolTip(f"Motor {idx} - {label}")
            checkbox.stateChanged.connect(lambda state, motor=idx: self.toggle_motor_selection(motor, state))
            self.motor_checkboxes[idx] = checkbox
            motor_selection_layout.addWidget(checkbox)
        
        # Çoklu motor kontrol butonları
        select_all_btn = QtWidgets.QPushButton('Hepsini Seç')
        deselect_all_btn = QtWidgets.QPushButton('Hepsini Kaldır')
        select_all_btn.clicked.connect(self.select_all_motors)
        deselect_all_btn.clicked.connect(self.deselect_all_motors)
        
        motor_selection_layout.addWidget(select_all_btn)
        motor_selection_layout.addWidget(deselect_all_btn)
        
        grid.addLayout(motor_selection_layout, row, 0, 1, 6)
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
        # Hız kontrolü sinyalleri
        self.slider_speed.valueChanged.connect(self._on_speed_value_changed)
        self.slider_speed.sliderReleased.connect(self._send_speed_to_arduino)

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
        # Arduino'dan gelen mesajları analiz et ve UI'yi güncelle
        text_lower = text.lower()
        
        # Kayıt durumu güncellemeleri
        if '[rec] kayit basladi' in text_lower:
            self.lbl_rec.setText('Kayıt: Açık')
        elif '[rec] kayit durdu' in text_lower:
            self.lbl_rec.setText('Kayıt: Kapalı')
        elif '[play] oynatma basladi' in text_lower:
            self.lbl_play.setText('Oynatma: Açık')
        elif '[play] oynatma durdu' in text_lower:
            self.lbl_play.setText('Oynatma: Kapalı')
        elif 'loop:' in text_lower and 'acik' in text_lower:
            self.lbl_loop.setText('Loop: Açık')
        elif 'loop:' in text_lower and 'kapali' in text_lower:
            self.lbl_loop.setText('Loop: Kapalı')
        
        # Basit ve guvenli: dogrudan ekle
        self.log.appendPlainText(text)

    def on_connected(self, port: str):
        self.status_lbl.setText(f'Durum: Bağlı ({port}) - Motor Seçilmedi')
        self.log.appendPlainText(f"[INFO] Bağlandı: {port}\n")

    def on_disconnected(self):
        self.status_lbl.setText('Durum: Bağlı değil')
        self.log.appendPlainText("[WARN] Bağlantı koptu, yeniden denenecek...\n")

    def send(self, ch: str):
        self.worker.send_char(ch)
        # Genel log
        self._append_operation(f"SEND {ch}")

    # --- Speed control helpers ---
    def _on_speed_value_changed(self, val: int):
        self.lbl_speed_value.setText(str(val))

    def _send_speed_to_arduino(self):
        val = self.slider_speed.value()
        # Protokol: 'Z' + 4 haneli mikro-saniye (örn: Z1800)
        cmd = f"Z{val:04d}"
        for ch in cmd:
            self.send(ch)

    def _rec_play_action(self, code: str):
        # Arduino'nun kayıt/oynatma sistemini kullan
        if code == 'R':
            self.send('R')  # Arduino'ya kayıt başlat komutu gönder
            self.lbl_rec.setText('Kayıt: Açık')
            self._append_operation('ARDUINO REC START')
            return
        if code == 'T':
            self.send('T')  # Arduino'ya kayıt durdur komutu gönder
            self.lbl_rec.setText('Kayıt: Kapalı')
            self._append_operation('ARDUINO REC STOP')
            return
        if code == 'P':
            self.send('P')  # Arduino'ya oynatma başlat komutu gönder
            self.lbl_play.setText('Oynatma: Açık')
            self._append_operation('ARDUINO PLAY START')
            return
        if code == 'S':
            self.send('S')  # Arduino'ya oynatma durdur komutu gönder
            self.lbl_play.setText('Oynatma: Kapalı')
            self._append_operation('ARDUINO PLAY STOP')
            return
        if code == 'L':
            self.send('L')  # Arduino'ya loop toggle komutu gönder
            self._append_operation('ARDUINO LOOP TOGGLE')
            return
        if code == 'V':
            self.send('V')  # Arduino'dan bilgi al
            self._append_operation('ARDUINO INFO REQUEST')
            return

    # --- Arduino kayıt/oynatma sistemi kullanılıyor ---


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

    # --- Çoklu Motor Seçimi Fonksiyonları ---
    def toggle_motor_selection(self, motor: int, state: int):
        """Motor seçimini toggle et"""
        if state == 2:  # Qt.Checked
            self.selected_motors.add(motor)
            self._append_operation(f"MOTOR M{motor} SELECTED")
        else:
            self.selected_motors.discard(motor)
            self._append_operation(f"MOTOR M{motor} DESELECTED")
        
        # UI güncellemesi
        self.update_motor_status()

    def select_all_motors(self):
        """Tüm motorları seç"""
        for motor in range(1, 7):  # 1-6 arası motorlar
            self.selected_motors.add(motor)
            self.motor_checkboxes[motor].setChecked(True)
        self._append_operation("ALL MOTORS SELECTED")
        self.update_motor_status()

    def deselect_all_motors(self):
        """Tüm motor seçimlerini kaldır"""
        for motor in range(1, 7):  # 1-6 arası motorlar
            self.selected_motors.discard(motor)
            self.motor_checkboxes[motor].setChecked(False)
        self._append_operation("ALL MOTORS DESELECTED")
        self.update_motor_status()

    def update_motor_status(self):
        """Seçili motor durumunu UI'da göster"""
        if self.selected_motors:
            motors_str = ", ".join([f"M{m}" for m in sorted(self.selected_motors)])
            self.status_lbl.setText(f'Durum: Bağlı - Seçili Motorlar: {motors_str}')
        else:
            self.status_lbl.setText('Durum: Bağlı - Motor Seçilmedi')

    def send_to_selected_motors(self, command: str):
        """Seçili tüm motorlara komut gönder"""
        if not self.selected_motors:
            self._append_operation("NO MOTORS SELECTED")
            return
        
        # Arduino'da motorlar aynı anda çalışabilir, bu yüzden hızlıca gönder
        for motor in sorted(self.selected_motors):
            # Motor seçimi için önce motor numarasını gönder
            self.send(str(motor))
            # Sonra komutu gönder
            self.send(command)
            # Çok kısa bekleme - Arduino'nun komutu işlemesi için
            QtCore.QThread.msleep(5)

    # --- Motor/Servo handlers with logging & reverse ---
    def select_motor(self, motor: int):
        self.active_motor = motor
        self.send(str(motor))
        self._append_operation(f"SELECT M{motor}")

    def handle_motion(self, code: str):
        # Çoklu motor kontrolü aktifse
        if self.selected_motors:
            self.handle_multi_motor_motion(code)
            return
        
        # Tek motor kontrolü (eski sistem)
        if not self.active_motor or self.active_motor not in (1, 2, 3, 4, 5):
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

    def handle_multi_motor_motion(self, code: str):
        """Çoklu motor için hareket kontrolü"""
        now = int(time.time() * 1000)
        stepper_motors = [m for m in self.selected_motors if m in (1, 2, 3, 4, 5)]
        
        if not stepper_motors:
            self._append_operation("NO STEPPER MOTORS SELECTED")
            return

        if code in ('d', 'a'):
            # Tüm seçili stepper motorları için segment başlat
            for motor in stepper_motors:
                # Kapanmamis segment varsa kapat
                if self.segment_start_ms[motor] is not None and self.segment_dir[motor] is not None:
                    duration = now - int(self.segment_start_ms[motor])
                    inv_dir = 2 if self.segment_dir[motor] == 1 else 1
                    self.reverse_actions.append((motor, inv_dir, max(0, duration)))
                    self._append_operation(f"M{motor} STOP duration={duration}ms")
                
                # Yeni segment baslat
                self.segment_start_ms[motor] = now
                self.segment_dir[motor] = 1 if code == 'd' else 2
            
            motors_str = ", ".join([f"M{m}" for m in stepper_motors])
            self._append_operation(f"MULTI MOTOR START {motors_str} dir={'ILERI' if code=='d' else 'GERI'}")
            
            # Tüm motorlara komut gönder
            self.send_to_selected_motors(code)
            
        elif code == 'w':
            # Tüm seçili motorları durdur
            for motor in stepper_motors:
                if self.segment_start_ms[motor] is not None and self.segment_dir[motor] is not None:
                    duration = now - int(self.segment_start_ms[motor])
                    inv_dir = 2 if self.segment_dir[motor] == 1 else 1
                    self.reverse_actions.append((motor, inv_dir, max(0, duration)))
                    self._append_operation(f"M{motor} STOP duration={duration}ms")
                self.segment_start_ms[motor] = None
                self.segment_dir[motor] = None
            
            motors_str = ", ".join([f"M{m}" for m in stepper_motors])
            self._append_operation(f"MULTI MOTOR STOP {motors_str}")
            
            # Tüm motorlara dur komutu gönder
            self.send_to_selected_motors('w')

    def handle_servo(self, code: str):
        # Çoklu motor kontrolü aktifse
        if self.selected_motors:
            self.handle_multi_motor_servo(code)
            return
        
        # Tek motor kontrolü (eski sistem)
        if self.active_motor != 6:
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

    def handle_multi_motor_servo(self, code: str):
        """Çoklu motor için servo kontrolü"""
        servo_motors = [m for m in self.selected_motors if m == 6]
        
        if not servo_motors:
            self._append_operation("NO SERVO MOTOR SELECTED")
            return
        
        # Servo açısını güncelle
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
        
        self._append_operation(f"MULTI SERVO angle~{self.servo_angle_local}")
        
        # Servo komutunu gönder
        self.send_to_selected_motors(code)

    def return_to_home(self):
        # Çoklu motor kontrolü aktifse
        if self.selected_motors:
            self.return_to_home_multi()
            return
        
        # Tek motor kontrolü (eski sistem)
        # Servo -> 0 derece
        if self.active_motor != 6:
            self.select_motor(6)
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

    def return_to_home_multi(self):
        """Çoklu motor için home'a dönüş"""
        # Servo motorları varsa 0'a getir
        if 6 in self.selected_motors:
            self.send('6')  # Motor 6'yı seç
            self.send('c')  # Servo'yu 0'a getir
            self._append_operation('MULTI SERVO -> 0')

        # Stepper motorları için ters hareket
        stepper_motors = [m for m in self.selected_motors if m in (1, 2, 3, 4, 5)]
        if stepper_motors:
            # Ters hareketleri oynat
            for motor, inv_dir, duration in reversed(self.reverse_actions):
                if motor in stepper_motors:
                    self.send(str(motor))  # Motor seç
                    self.send('d' if inv_dir == 1 else 'a')  # Ters yönde hareket
                    QtWidgets.QApplication.processEvents()
                    QtCore.QThread.msleep(max(0, duration))
                    self.send('w')  # Dur
        
        self._append_operation('MULTI HOME DONE')
        # Temizle
        self.reverse_actions.clear()


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()


