import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools.remote_worker import RemoteWorker
from hedging_tools import portfolio_runner


class RemoteWorkerTests(unittest.TestCase):
    def worker(self, configure=None):
        self.clock = Mock(return_value=0)
        self.child = Mock()
        self.child.poll.return_value = None
        self.launch = Mock(return_value=self.child)
        self.publish = Mock()
        return RemoteWorker(self.clock, self.launch, configure or Mock(return_value=(123, 'secret')), self.publish)

    def test_enabled_starts_once_without_credentials_in_arguments(self):
        worker = self.worker()
        worker.tick()
        worker.tick()
        self.launch.assert_called_once()
        self.assertNotIn('secret', str(self.launch.call_args))
        self.assertTrue(self.launch.call_args.kwargs['start_new_session'])
        self.assertIn('hedging_tools.telegram_remote', self.launch.call_args.args[0])
        worker.close()
        self.child.terminate.assert_called_once()
        worker.tick()
        self.launch.assert_called_once()

    def test_disabled_or_invalid_configuration_does_not_launch(self):
        worker = self.worker(Mock(side_effect=ValueError('Remote disabled or incomplete')))
        worker.tick()
        worker.close()
        self.launch.assert_not_called()
        self.assertEqual(self.publish.call_args.args[0]['status'], 'REMOTE_NOT_STARTED')

    def test_child_exit_restarts_after_backoff(self):
        worker = self.worker()
        worker.tick()
        self.child.poll.return_value = 1
        worker.tick()
        self.clock.return_value = 59
        worker.tick()
        self.launch.assert_called_once()
        self.clock.return_value = 60
        worker.tick()
        self.assertEqual(self.launch.call_count, 2)

    def test_launch_failure_retries_without_raising(self):
        worker = self.worker()
        self.launch.side_effect = OSError('Cannot launch')
        worker.tick()
        worker.tick()
        self.launch.assert_called_once()
        self.clock.return_value = 60
        worker.tick()
        self.assertEqual(self.launch.call_count, 2)

    def test_shutdown_kills_and_reaps_unresponsive_child(self):
        worker = self.worker()
        worker.tick()
        self.child.wait.side_effect = [subprocess.TimeoutExpired('remote', 5), 0]
        worker.close()
        self.child.kill.assert_called_once()
        self.assertEqual(self.child.wait.call_count, 2)
        self.assertIsNone(worker.child)

    def test_run_starts_remote_and_closes_it_on_loop_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(portfolio_runner, 'RUNTIME_DIR', Path(directory)), \
                patch.object(portfolio_runner.signal, 'signal'), \
                patch.object(portfolio_runner, 'Budget') as budget, \
                patch.object(portfolio_runner, 'Controls'), \
                patch.object(portfolio_runner, 'Store'), \
                patch.object(portfolio_runner, 'Portfolio'), \
                patch.object(portfolio_runner, 'OKXDemo'), \
                patch.object(portfolio_runner, 'OKXPublic'), \
                patch.object(portfolio_runner, 'HistoryWorker'), \
                patch.object(portfolio_runner, 'RemoteWorker') as remote, \
                patch.object(portfolio_runner, 'EngineEvents'), \
                patch.object(portfolio_runner, 'loop') as loop, \
                patch('sys.argv', ['run']):
            budget.return_value.snapshot.return_value = {}
            def fail(*args, **kwargs):
                kwargs['background']()
                raise RuntimeError('test shutdown')
            loop.side_effect = fail
            with self.assertRaisesRegex(RuntimeError, 'test shutdown'):
                portfolio_runner.main()
            remote.return_value.tick.assert_called_once()
            remote.return_value.close.assert_called_once()
