import sys, time

sys.stderr.write("owned pipe-write stall fixture started")
sys.stderr.flush()
while True:
    time.sleep(10)
