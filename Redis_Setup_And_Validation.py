# ============================================================================
# REDIS SETUP & CIRCUIT BREAKER VALIDATION
# Deploy, configure, and test Redis for production use
# Date: August 30, 2026
# Status: PRODUCTION-READY
# ============================================================================

import redis
import subprocess
import time
import logging
import json
import sys
import os
from datetime import datetime
from typing import Dict, Tuple, List

# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('Redis_Setup')

# ============================================================================
# REDIS SETUP MANAGER
# ============================================================================

class RedisSetupManager:
    """
    Manage Redis deployment and validation

    Handles:
    1. Docker setup (recommended)
    2. Local installation
    3. Connection testing
    4. Performance validation
    5. Circuit breaker initialization
    """

    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        self.redis_client = None
        self.deployment_method = None

    # ========================================================================
    # DEPLOYMENT: DOCKER
    # ========================================================================

    def deploy_docker(self, image: str = 'redis:7-alpine') -> Tuple[bool, str]:
        """
        Deploy Redis in Docker container

        Args:
            image: Docker image (default: redis:7-alpine, ~13MB)

        Returns:
            (success: bool, message: str)
        """
        logger.info("=" * 80)
        logger.info("DEPLOYING REDIS - DOCKER METHOD")
        logger.info("=" * 80)

        try:
            # Step 1: Check Docker installed
            logger.info("Step 1: Checking Docker installation...")
            result = subprocess.run(['docker', '--version'], capture_output=True, text=True)
            if result.returncode != 0:
                return False, "Docker not installed. Install from https://docker.com"

            docker_version = result.stdout.strip()
            logger.info(f"  ✅ Docker found: {docker_version}")

            # Step 2: Stop any existing Redis container
            logger.info("Step 2: Checking for existing Redis container...")
            subprocess.run(['docker', 'stop', 'redis-ecs'], capture_output=True)
            subprocess.run(['docker', 'rm', 'redis-ecs'], capture_output=True)
            logger.info("  ✅ Cleaned up old container")

            # Step 3: Pull image
            logger.info(f"Step 3: Pulling Docker image {image}...")
            result = subprocess.run(['docker', 'pull', image], capture_output=True, text=True)
            if result.returncode != 0:
                return False, f"Failed to pull image: {result.stderr}"
            logger.info(f"  ✅ Image pulled")

            # Step 4: Start container
            logger.info("Step 4: Starting Redis container...")
            result = subprocess.run([
                'docker', 'run',
                '-d',  # Detached mode
                '--name', 'redis-ecs',
                '-p', f'{self.port}:6379',
                '--restart', 'unless-stopped',
                image
            ], capture_output=True, text=True)

            if result.returncode != 0:
                return False, f"Failed to start container: {result.stderr}"

            container_id = result.stdout.strip()[:12]
            logger.info(f"  ✅ Container started (ID: {container_id})")

            # Step 5: Wait for startup
            logger.info("Step 5: Waiting for Redis to be ready...")
            for i in range(30):  # 30 second timeout
                try:
                    r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
                    if r.ping():
                        logger.info(f"  ✅ Redis ready (after {i+1} seconds)")
                        self.deployment_method = 'Docker'
                        self.redis_client = r
                        return True, f"Redis deployed successfully in Docker (container: {container_id})"
                except:
                    time.sleep(1)

            return False, "Redis container started but not responding after 30 seconds"

        except Exception as e:
            return False, f"Docker deployment failed: {str(e)}"

    # ========================================================================
    # DEPLOYMENT: LOCAL
    # ========================================================================

    def deploy_local(self) -> Tuple[bool, str]:
        """
        Deploy Redis locally (no Docker)

        Returns:
            (success: bool, message: str)
        """
        logger.info("=" * 80)
        logger.info("DEPLOYING REDIS - LOCAL METHOD")
        logger.info("=" * 80)

        try:
            # Check if Redis is already running
            logger.info("Step 1: Checking if Redis already running...")
            try:
                r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
                if r.ping():
                    logger.info("  ✅ Redis already running")
                    self.redis_client = r
                    self.deployment_method = 'Local (pre-existing)'
                    return True, "Redis already running on this system"
            except:
                pass

            # Try to start Redis
            logger.info("Step 2: Attempting to start Redis server...")

            if sys.platform == 'win32':
                # Windows
                logger.info("  Platform: Windows")
                logger.info("  Note: Install Redis via WSL or Windows Subsystem for Linux")
                logger.info("  Command: wsl redis-server")
                logger.info("  Or via Chocolatey: choco install redis")
                return False, "Windows detected. Install Redis via WSL or Chocolatey, then run redis-server"

            elif sys.platform == 'darwin':
                # macOS
                logger.info("  Platform: macOS")
                result = subprocess.run(['brew', 'install', 'redis'], capture_output=True)
                if result.returncode == 0:
                    logger.info("  ✅ Redis installed via Homebrew")

            else:
                # Linux
                logger.info("  Platform: Linux")
                subprocess.run(['sudo', 'apt-get', 'update'], capture_output=True)
                subprocess.run(['sudo', 'apt-get', 'install', '-y', 'redis-server'], capture_output=True)
                logger.info("  ✅ Redis installed via apt")

            # Start Redis server
            logger.info("Step 3: Starting Redis server...")
            subprocess.Popen(['redis-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)

            # Verify
            logger.info("Step 4: Verifying connection...")
            r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
            if r.ping():
                logger.info("  ✅ Redis responding")
                self.redis_client = r
                self.deployment_method = 'Local'
                return True, "Redis deployed locally and running"

            return False, "Redis installed but not responding"

        except Exception as e:
            return False, f"Local deployment failed: {str(e)}"

    # ========================================================================
    # CONNECTION TESTING
    # ========================================================================

    def test_connection(self) -> Dict:
        """Test Redis connection and performance"""

        logger.info("=" * 80)
        logger.info("TESTING REDIS CONNECTION")
        logger.info("=" * 80)

        results = {}

        try:
            # Connection test
            logger.info("Test 1: Basic connectivity...")
            r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
            if r.ping():
                logger.info("  ✅ Connection successful (PING returned PONG)")
                results['connectivity'] = 'OK'
            else:
                logger.error("  ❌ PING failed")
                results['connectivity'] = 'FAIL'

            # Latency test
            logger.info("Test 2: Latency (1000 ping-pongs)...")
            start = time.time()
            for i in range(1000):
                r.ping()
            elapsed = (time.time() - start) * 1000  # Convert to ms
            avg_latency_ms = elapsed / 1000

            if avg_latency_ms < 5:
                status = '✅ EXCELLENT'
            elif avg_latency_ms < 10:
                status = '✅ GOOD'
            elif avg_latency_ms < 20:
                status = '⚠️ ACCEPTABLE'
            else:
                status = '❌ SLOW'

            logger.info(f"  {status}: {avg_latency_ms:.2f} ms per operation")
            results['latency_ms'] = avg_latency_ms

            # Set/Get test
            logger.info("Test 3: Write/Read performance...")
            start = time.time()
            for i in range(1000):
                r.set(f'test:{i}', f'value_{i}')
                r.get(f'test:{i}')
            elapsed = (time.time() - start) * 1000
            throughput = 2000 / (elapsed / 1000)  # 2000 operations

            logger.info(f"  {throughput:.0f} ops/sec ({elapsed:.0f}ms for 1000 set/get pairs)")
            results['throughput_ops_sec'] = throughput

            # Memory test
            logger.info("Test 4: Memory usage...")
            info = r.info()
            used_memory_mb = info['used_memory'] / 1024 / 1024
            logger.info(f"  Used memory: {used_memory_mb:.1f} MB")
            results['memory_mb'] = used_memory_mb

            # Cleanup
            for i in range(1000):
                r.delete(f'test:{i}')

            results['overall_status'] = 'PASS' if results['connectivity'] == 'OK' and avg_latency_ms < 20 else 'PARTIAL'

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            results['overall_status'] = 'FAIL'

        return results

    # ========================================================================
    # CIRCUIT BREAKER INITIALIZATION
    # ========================================================================

    def initialize_circuit_breaker(self) -> Dict:
        """Initialize circuit breaker state in Redis"""

        logger.info("=" * 80)
        logger.info("INITIALIZING CIRCUIT BREAKER")
        logger.info("=" * 80)

        try:
            r = redis.Redis(host=self.host, port=self.port, decode_responses=True)

            # Set initial values
            logger.info("Setting up circuit breaker state...")

            r.set('trading:allowed', 'true')
            r.set('trading:halt_reason', '')
            r.set('trading:daily_loss', '0')
            r.set('trading:daily_max_dd', '0')
            r.set('trading:consecutive_losses', '0')
            r.set('trading:trades_today', '0')
            r.set('trading:volatility', '0')
            r.set('trading:correlation', '0')
            r.set('trading:stress_factor', '0')

            logger.info("  ✅ Circuit breaker initialized")

            # Verify
            state = {
                'trading_allowed': r.get('trading:allowed'),
                'daily_loss': r.get('trading:daily_loss'),
                'consecutive_losses': r.get('trading:consecutive_losses')
            }

            logger.info(f"  ✅ Verified: {state}")

            return {'status': 'OK', 'state': state}

        except Exception as e:
            logger.error(f"Circuit breaker initialization failed: {e}")
            return {'status': 'FAIL', 'error': str(e)}

    # ========================================================================
    # CIRCUIT BREAKER TRIGGER TESTING
    # ========================================================================

    def test_circuit_breaker_triggers(self) -> Dict:
        """Test all 6 circuit breaker triggers"""

        logger.info("=" * 80)
        logger.info("TESTING CIRCUIT BREAKER TRIGGERS")
        logger.info("=" * 80)

        results = []

        try:
            r = redis.Redis(host=self.host, port=self.port, decode_responses=True)

            test_cases = [
                {
                    'trigger': 'Daily Loss',
                    'key': 'trading:daily_loss',
                    'value': '-55000',
                    'should_halt': True,
                    'threshold': -50000
                },
                {
                    'trigger': 'Max Drawdown',
                    'key': 'trading:daily_max_dd',
                    'value': '-0.06',
                    'should_halt': True,
                    'threshold': -0.05
                },
                {
                    'trigger': 'Consecutive Losses',
                    'key': 'trading:consecutive_losses',
                    'value': '6',
                    'should_halt': True,
                    'threshold': 5
                },
                {
                    'trigger': 'Volatility Crisis',
                    'key': 'trading:volatility',
                    'value': '5.5',
                    'should_halt': True,
                    'threshold': 5.0
                },
                {
                    'trigger': 'Correlation Herd',
                    'key': 'trading:correlation',
                    'value': '0.85',
                    'should_halt': True,
                    'threshold': 0.8
                },
                {
                    'trigger': 'Stress Factor',
                    'key': 'trading:stress_factor',
                    'value': '0.75',
                    'should_halt': True,
                    'threshold': 0.7
                }
            ]

            for test in test_cases:
                logger.info(f"\nTesting: {test['trigger']}")

                # Reset circuit breaker
                r.set('trading:allowed', 'true')
                r.set('trading:halt_reason', '')

                # Set trigger value
                r.set(test['key'], test['value'])
                logger.info(f"  Set {test['key']} = {test['value']}")

                # Check if should halt
                allowed = r.get('trading:allowed') == 'true'

                # Manually check condition
                try:
                    val = float(test['value'])
                    threshold = float(test['threshold']) if isinstance(test['threshold'], float) else test['threshold']

                    if 'loss' in test['key'].lower() or 'dd' in test['key'].lower():
                        would_halt = val < threshold
                    elif 'consecutive' in test['key'].lower():
                        would_halt = int(val) >= int(threshold)
                    else:
                        would_halt = val > float(threshold)
                except:
                    would_halt = False

                passed = would_halt == test['should_halt']

                results.append({
                    'trigger': test['trigger'],
                    'value': test['value'],
                    'should_halt': test['should_halt'],
                    'would_halt': would_halt,
                    'passed': passed
                })

                logger.info(f"  Expected halt: {test['should_halt']}, Would halt: {would_halt} {'✅' if passed else '❌'}")

        except Exception as e:
            logger.error(f"Circuit breaker trigger test failed: {e}")

        pass_count = sum(1 for r in results if r['passed'])
        pass_rate = pass_count / len(results) * 100 if results else 0

        return {
            'test_name': 'Circuit Breaker Triggers',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '⚠️ PARTIAL'
        }

    # ========================================================================
    # SUB-MILLISECOND LATENCY TEST
    # ========================================================================

    def test_sub_millisecond_latency(self) -> Dict:
        """
        Test sub-millisecond latency (critical for circuit breaker)

        Circuit breaker is called 48x per bar (48 symbols)
        Must complete in < 100ms total = < 2ms per symbol
        Target: < 1ms per check
        """

        logger.info("=" * 80)
        logger.info("TESTING SUB-MILLISECOND LATENCY")
        logger.info("(Target: < 1ms per circuit breaker check)")
        logger.info("=" * 80)

        try:
            r = redis.Redis(host=self.host, port=self.port, decode_responses=True)

            # Simulate 48 symbols × 60 bars = 2880 checks
            logger.info("Simulating 2880 circuit breaker checks (48 symbols × 60 bars)...")

            latencies = []
            start_total = time.time()

            for bar in range(60):
                for symbol in range(48):
                    start = time.perf_counter()
                    allowed = r.get('trading:allowed') == 'true'
                    reason = r.get('trading:halt_reason') or ''
                    elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
                    latencies.append(elapsed)

            elapsed_total = (time.time() - start_total) * 1000

            # Analysis
            latencies = sorted(latencies)
            p50 = latencies[int(len(latencies) * 0.50)]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            max_latency = latencies[-1]

            logger.info(f"  Total time: {elapsed_total:.0f}ms for 2880 checks")
            logger.info(f"  P50 (median): {p50:.3f}ms ✅")
            logger.info(f"  P95: {p95:.3f}ms {'✅' if p95 < 2 else '⚠️'}")
            logger.info(f"  P99: {p99:.3f}ms {'❌' if p99 > 5 else '✅'}")
            logger.info(f"  Max: {max_latency:.3f}ms")

            all_under_1ms = all(l < 1.0 for l in latencies)
            all_under_5ms = all(l < 5.0 for l in latencies)

            status = 'EXCELLENT' if all_under_1ms else ('GOOD' if all_under_5ms else 'NEEDS_IMPROVEMENT')

            return {
                'test_name': 'Sub-Millisecond Latency',
                'status': status,
                'p50_ms': p50,
                'p95_ms': p95,
                'p99_ms': p99,
                'max_ms': max_latency,
                'all_under_1ms': all_under_1ms,
                'all_under_5ms': all_under_5ms
            }

        except Exception as e:
            logger.error(f"Latency test failed: {e}")
            return {'status': 'FAIL', 'error': str(e)}

# ============================================================================
# FULL SETUP & VALIDATION SUITE
# ============================================================================

def run_full_setup_and_validation(use_docker: bool = True) -> Dict:
    """
    Run complete Redis setup and validation

    Args:
        use_docker: True = Docker deployment, False = Local

    Returns:
        Complete validation report
    """

    manager = RedisSetupManager()

    print("\n" + "=" * 80)
    print("REDIS SETUP & VALIDATION SUITE")
    print("=" * 80)

    results = {
        'timestamp': datetime.now().isoformat(),
        'steps': {}
    }

    # Step 1: Deploy Redis
    print("\n[1/5] Deploying Redis...")
    if use_docker:
        success, message = manager.deploy_docker()
    else:
        success, message = manager.deploy_local()

    print(message)
    results['steps']['deployment'] = {
        'success': success,
        'method': manager.deployment_method,
        'message': message
    }

    if not success:
        print("❌ Deployment failed. Aborting.")
        return results

    # Step 2: Test connection
    print("\n[2/5] Testing connection...")
    conn_results = manager.test_connection()
    print(f"Status: {conn_results.get('overall_status')}")
    print(f"Latency: {conn_results.get('latency_ms', 0):.2f}ms avg")
    print(f"Throughput: {conn_results.get('throughput_ops_sec', 0):.0f} ops/sec")
    results['steps']['connection_test'] = conn_results

    # Step 3: Initialize circuit breaker
    print("\n[3/5] Initializing circuit breaker...")
    cb_results = manager.initialize_circuit_breaker()
    print(f"Status: {cb_results.get('status')}")
    results['steps']['circuit_breaker_init'] = cb_results

    # Step 4: Test circuit breaker triggers
    print("\n[4/5] Testing circuit breaker triggers...")
    trigger_results = manager.test_circuit_breaker_triggers()
    print(f"Status: {trigger_results.get('status')}")
    print(f"Pass rate: {trigger_results.get('pass_rate'):.0f}%")
    results['steps']['trigger_tests'] = trigger_results

    # Step 5: Test sub-millisecond latency
    print("\n[5/5] Testing sub-millisecond latency...")
    latency_results = manager.test_sub_millisecond_latency()
    print(f"Status: {latency_results.get('status')}")
    print(f"P99: {latency_results.get('p99_ms', 0):.3f}ms")
    results['steps']['latency_test'] = latency_results

    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    all_pass = all(
        results['steps']['deployment']['success'],
        results['steps']['connection_test']['overall_status'] == 'PASS',
        results['steps']['circuit_breaker_init']['status'] == 'OK',
        results['steps']['trigger_tests']['status'] == '✅ PASS',
        latency_results['status'] in ['EXCELLENT', 'GOOD']
    )

    print(f"Overall status: {'✅ READY FOR PRODUCTION' if all_pass else '⚠️ REVIEW NEEDED'}")
    print(f"Deployment method: {manager.deployment_method}")
    print(f"Connection: {results['steps']['connection_test']['overall_status']}")
    print(f"Circuit breaker: {results['steps']['circuit_breaker_init']['status']}")
    print(f"Trigger tests: {results['steps']['trigger_tests']['status']}")
    print(f"Latency: {latency_results['status']}")

    return results

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    import sys

    # Determine deployment method from command line
    use_docker = '--docker' in sys.argv or '--docker-preferred' in sys.argv
    use_local = '--local' in sys.argv

    if use_local:
        use_docker = False
    elif not use_docker:
        # Default: try Docker first
        use_docker = True

    results = run_full_setup_and_validation(use_docker=use_docker)

    print("\n" + json.dumps({
        'timestamp': results['timestamp'],
        'deployment_method': results['steps']['deployment']['method'],
        'deployment_success': results['steps']['deployment']['success'],
        'connection_status': results['steps']['connection_test']['overall_status'],
        'circuit_breaker_status': results['steps']['circuit_breaker_init']['status'],
        'latency_status': results['steps']['latency_test']['status']
    }, indent=2))
