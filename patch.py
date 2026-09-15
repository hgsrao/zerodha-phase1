with open('test_harness_v34_b_resilience.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '        self.successful_calls_by_operation = {}'
injection = target + '\n        self._ltp = {"NSE:RELIANCE": {"last_price": 1300.0}, "RELIANCE": {"last_price": 1300.0}}\n        self._depth = {"NSE:RELIANCE": {"buy": [{"quantity": 100}], "sell": [{"quantity": 100}]}}'

# Inject the fixtures safely into __init__
if 'self._ltp = {' not in content.split('def _record_call')[0]:
    content = content.replace(target, injection)
    with open('test_harness_v34_b_resilience.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Harness patched successfully.')
else:
    print('Harness already patched.')
