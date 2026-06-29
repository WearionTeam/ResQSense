from magnetometer_controller import MagnetometerController
import time

m = MagnetometerController()
print("Available:", m.available())
if m.last_error:
    print("Erro:", m.last_error)

m.start()
print("Roda o sensor agora...")
for _ in range(20):
    time.sleep(0.3)
    print(f"Heading: {m.get_heading_degrees():.1f}")
m.stop()
