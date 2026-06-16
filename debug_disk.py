import psutil
import time

print("--- DIAGNOSTICO: PSUTIL (KERNEL DIRECTO) ---")
print("Genera carga en el disco AHORA...")
print("-" * 50)


# Buscamos el disco físico donde está C:
# Generalmente es PhysicalDrive0
disco_objetivo = "PhysicalDrive0"

try:
    while True:
        # Tomar foto 1
        io1 = psutil.disk_io_counters(perdisk=True).get(disco_objetivo)
        time.sleep(1)
        # Tomar foto 2
        io2 = psutil.disk_io_counters(perdisk=True).get(disco_objetivo)
        
        if io1 and io2:
            # Calcular Deltas
            read_count = io2.read_count - io1.read_count
            write_count = io2.write_count - io1.write_count
            total_ops = read_count + write_count
            
            # Tiempo activo (en ms)
            read_time = io2.read_time - io1.read_time
            write_time = io2.write_time - io1.write_time
            total_time = read_time + write_time
            
            # CALCULO DE LATENCIA REAL
            if total_ops > 0:
                avg_latency = total_time / total_ops
            else:
                avg_latency = 0.0
                
            print(f"Ops: {total_ops} | Tiempo Activo: {total_time}ms | >> LATENCIA: {avg_latency:.2f} ms <<")
        else:
            print(f"No se encuentra {disco_objetivo}. Discos: {list(psutil.disk_io_counters(perdisk=True).keys())}")
            
except KeyboardInterrupt:
    print("\nDetenido.")