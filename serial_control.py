import sys
import time
import select
from typing import Any


HELP_TEXT = (
    "\nKomutlar (Arduino ile birebir):\n"
    "  1-6 : motor secimi (1-5 step motor, 6 servo)\n"
    "  d   : ileri (step) / +60° (servo)\n"
    "  a   : geri (step)  / -60° (servo)\n"
    "  w   : dur (step)   / bekle (servo)\n"
    "  [   : +15° (servo)\n"
    "  ]   : -15° (servo)\n"
    "  c   : 0° (servo)\n"
    "  R/T : kayit baslat/durdur\n"
    "  P/S : oynat baslat/durdur\n"
    "  L   : loop toggle\n"
    "  Z#### : step hızı (µs), örn: Z0400 .. Z4000\n"
    "  V   : bilgi\n"
    "  h   : yardim\n"
    "  q   : cikis\n"
    "\nÇoklu Motor Kontrolü:\n"
    "  Birden fazla motor seçip aynı anda kontrol edebilirsiniz.\n"
    "  Arduino kodunda motorlar aynı anda çalışabilir.\n"
)


def list_ports():
    try:
        from serial.tools import list_ports as _list_ports  # type: ignore
    except Exception:
        print("pyserial gerekli. Kurulum: pip install pyserial")
        return
    ports = list(_list_ports.comports())
    if not ports:
        print("Hic seri port bulunamadi.")
    for p in ports:
        print(f"- {p.device}  ({p.description})")


def open_serial(port: str, baud: int = 9600, timeout: float = 0.1) -> Any:
    try:
        import serial  # type: ignore
    except Exception as exc:
        print("pyserial gerekli. Kurulum: pip install pyserial")
        raise
    ser = serial.Serial(port, baudrate=baud, timeout=timeout)
    # Arduino reset beklemesi icin kucuk bir gecikme
    time.sleep(2.0)
    return ser


def forward_serial_output(ser: Any):
    """Seriden gelen satirlari ekrana yaz (non-blocking)."""
    try:
        waiting = getattr(ser, "in_waiting", 0)
        data = ser.read(waiting or 1)
        if data:
            sys.stdout.write(data.decode(errors="ignore"))
            sys.stdout.flush()
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        print("Kullanim: python serial_control.py COMx [baud]")
        print("Ornek:   python serial_control.py COM3 9600")
        print("Mevcut portlar:")
        list_ports()
        return

    port = sys.argv[1]
    baud = int(sys.argv[2]) if len(sys.argv) >= 3 else 9600

    print(f"Baglaniyor: {port} @ {baud} ...")
    try:
        ser = open_serial(port, baud)
    except Exception as exc:
        print(f"Baglanti hatasi: {exc}")
        return

    print("Baglandi. 'h' yardim, 'q' cikis.")
    print(HELP_TEXT)

    try:
        while True:
            forward_serial_output(ser)

            # Konsoldan tek karakter oku (non-blocking icin Windows'ta msvcrt kullanilabilir)
            ch = None
            if sys.platform.startswith("win"):
                try:
                    import msvcrt  # type: ignore
                    if msvcrt.kbhit():
                        b = msvcrt.getwch()  # Unicode char
                        ch = b
                except Exception:
                    pass
            else:
                # POSIX: basit bir input prompt'u ile (blocking). Isterseniz termios ile gelistirin.
                ch = sys.stdin.read(1) if sys.stdin in select.select([sys.stdin], [], [], 0.02)[0] else None

            if not ch:
                time.sleep(0.02)
                continue

            if ch == "q":
                print("\nCikis...")
                break
            if ch == "h":
                print(HELP_TEXT)
                continue

            # Gecerli komutlari seriye gonder
            # Arduino tarafinda tek karakterler bekleniyor
            ser.write(ch.encode("utf-8", errors="ignore"))
            # Yeni satir GEREKMIYOR; Arduino tek char okuyor. Isterseniz:\n
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()


