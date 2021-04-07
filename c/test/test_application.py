from argparse import ArgumentParser
import copy
import os
import signal
import subprocess
import sys
import threading


class TestApplication(object):
    TEST_PASSED = 0
    TEST_FAILED = 1
    ARGUMENT_ERROR = 2
    EXECUTION_ERROR = 3
    NONZERO_EXIT = 4

    def __init__(self, application_name):
        self.root_dir = os.path.abspath(os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
        self.application_name = application_name

        # Define and parse arguments.
        self.parser = ArgumentParser(usage='%(prog)s [OPTIONS]...')

        self.parser.add_argument(
            '-p', '--path', metavar='PATH',
            help="The path to the application to be run.")
        self.parser.add_argument(
            '--polaris-api-key', metavar='KEY',
            help="The Polaris API key to be used. Ignored if the POLARIS_API_KEY environment variable is specified.")
        self.parser.add_argument(
            '-t', '--timeout', metavar='SEC', type=float, default=30.0,
            help="The maximum test duration (in seconds).")
        self.parser.add_argument(
            '--tool', metavar='TOOL', default='bazel',
            help="The tool used to compile the application (bazel, cmake, make), used to determine the default "
            "application path. Ignored if --path is specified.")
        self.parser.add_argument(
            '--unique-id', metavar='ID', default='test_' + application_name,
            help="The unique ID to assign to this instance.")

        self.options = None

        self.default_command = ['%(path)s', '%(polaris_api_key)s', '%(unique_id)s']
        self.program_args = []

        self.proc = None

    def parse_args(self):
        self.options = self.parser.parse_args()

        api_key_env = os.getenv('POLARIS_API_KEY')
        if api_key_env is not None:
            self.options.polaris_api_key = api_key_env
        elif self.options.polaris_api_key is None:
            print('Error: Polaris API key not specified.')
            sys.exit(self.ARGUMENT_ERROR)

        if self.options.path is None:
            if self.options.tool == 'bazel':
                self.options.path = os.path.join(self.root_dir, 'bazel-bin/examples', self.application_name)
            elif self.options.tool == 'cmake':
                self.options.path = os.path.join(self.root_dir, 'build/examples', self.application_name)
            elif self.options.tool == 'make':
                self.options.path = os.path.join(self.root_dir, 'examples', self.application_name)
            else:
                print('Error: Unsupported --tool value.')
                sys.exit(self.ARGUMENT_ERROR)

        if len(self.options.unique_id) > 36:
            self.options.unique_id = self.options.unique_id[:36]
            print("Unique ID too long. Truncating to '%s'." % self.options.unique_id)

        return self.options

    def run(self, return_result=False):
        # Setup the command to be run.
        command = copy.deepcopy(self.default_command)
        command.extend(self.program_args)
        for i in range(len(command)):
            if command[i] == '%(polaris_api_key)s':
                command[i] = '<POLARIS_API_KEY>'
            else:
                command[i] = command[i] % self.options.__dict__

        print('Executing: %s' % ' '.join(command))

        command.insert(0, 'stdbuf')
        command.insert(1, '-o0')
        for i in range(len(command)):
            if command[i] == '<POLARIS_API_KEY>':
                command[i] = self.options.polaris_api_key

        # Run the command.
        def preexec_function():
            # Disable forwarding of SIGINT/SIGTERM from the parent process (this script) to the child process (the
            # application under test).
            os.setpgrp()

        self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                                     preexec_fn=preexec_function)

        # Capture SIGINT and SIGTERM and shutdown the application gracefully.
        def request_shutdown(sig, frame):
            self.stop()
            signal.signal(sig, signal.SIG_DFL)

        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)

        # Stop the test after a max duration.
        def timeout_elapsed():
            print('Maximum test duration (%.1f sec) elapsed.' % self.options.timeout)
            self.stop()

        watchdog = threading.Timer(self.options.timeout, timeout_elapsed)
        watchdog.start()

        # Check for a pass/fail condition and forward output to the console.
        while True:
            try:
                line = self.proc.stdout.readline().rstrip('\n')
                if line != '':
                    print(line.rstrip('\n'))
                    self.on_stdout(line)
                elif self.proc.poll() is not None:
                    exit_code = self.proc.poll()
                    break
            except KeyboardInterrupt:
                print('Execution interrupted unexpectedly.')
                if return_result:
                    return self.EXECUTION_ERROR
                else:
                    sys.exit(self.EXECUTION_ERROR)

        watchdog.cancel()
        self.proc = None

        result = self.check_pass_fail(exit_code)
        if result == self.TEST_PASSED:
            print('Test result: success')
        else:
            print('Test result: FAIL')

        if return_result:
            return result
        else:
            sys.exit(result)

    def stop(self):
        if self.proc is not None:
            print('Sending shutdown request to the application.')
            self.proc.terminate()

    def check_pass_fail(self, exit_code):
        if exit_code != 0:
            print('Application exited with non-zero exit code %s.' % repr(exit_code))
            return self.NONZERO_EXIT
        else:
            return self.TEST_PASSED

    def on_stdout(self, line):
        pass
