import re

with open("one_day_replay.log", "r") as f:
    lines = f.readlines()

print(f"Total log lines: {len(lines)}")
for line in lines[-30:]:  # Print last 30 lines where summary or final telemetry usually sits
    print(line, end="")
