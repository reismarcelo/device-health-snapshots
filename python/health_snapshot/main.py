# -*- mode: python; python-indent: 4 -*-
import ncs
from ncs.dp import Action
from _ncs.dp import action_set_timeout
import re
from time import time
from itertools import islice


# ---------------------------------------------
# ACTIONS
# ---------------------------------------------
class RunAssessment(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info("Action {}".format(name))
        action_set_timeout(uinfo, 240)

        try:
            # Run assessment
            with ncs.maapi.single_read_trans(uinfo.username, "system") as read_t:
                root = ncs.maagic.get_root(read_t)
                # kp sample: /ncs:services/snap:health-samples/device{IOS-0}
                kp_node = ncs.maagic.cd(root, kp)

                assessment_name = kp_node.snap__assessment

                results = run_assessment(root.devices.device[kp_node.snap__name],
                                         root.ncs__services.snap__health_samples.snap__setup.snap__stop_on_error,
                                         root.ncs__services.snap__health_samples.snap__assessment[assessment_name],
                                         self.log)

            # Save assessment sample
            with ncs.maapi.single_write_trans(uinfo.username, "system", db=ncs.OPERATIONAL) as write_t:
                root = ncs.maagic.get_root(write_t)
                kp_node = ncs.maagic.cd(root, kp)

                # Create a new sample
                timestamp = int(time())
                new_sample = kp_node.snap__sample.create(timestamp)

                for seq, result, passed in results:
                    new_cmd = new_sample.snap__command.create(seq)
                    new_cmd.snap__output = result
                    new_cmd.snap__passed = passed

                # Trim old samples
                key_list = [sample_entry.timestamp for sample_entry in kp_node.snap__sample]
                items_to_trim = len(key_list) - root.ncs__services.snap__health_samples.snap__setup.snap__max_samples
                if items_to_trim > 0:
                    for old_key in islice(sorted(key_list), items_to_trim):
                        del kp_node.snap__sample[old_key]
                    self.log.info('Trimmed {} sample(s)'.format(items_to_trim))

                write_t.apply()
                self.log.info("Saved assessment: {}, timestamp: {}".format(kp_node.snap__name, timestamp))

            # Figure out the outcome
            assessment_outcome = all(map(lambda entry: entry[2], results))
            if assessment_outcome:
                output.success = "Assessment completed without errors"
            else:
                failed_cmds = ', '.join([str(seq) for seq, result, passed in results if not passed])
                output.failure = "Assessment completed with errors. Commands that failed: {}".format(failed_cmds)

        except AssessmentError as e:
            output.failure = "Assessment error: {}".format(e)
            self.log.info("Assessment error: {}".format(e))


class RunLightAssessment(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info("Action {}".format(name))
        action_set_timeout(uinfo, 240)

        try:
            # Run assessment
            results = None
            with ncs.maapi.single_read_trans(uinfo.username, "system") as read_t:
                root = ncs.maagic.get_root(read_t)
                # kp sample: /ncs:services/snap:health-samples/device{IOS-0}
                kp_node = ncs.maagic.cd(root, kp)

                assessment_name = kp_node.snap__lightweight_assessment

                if assessment_name is None:
                    raise AssessmentError("No lightweight assessment defined for {}".format(kp_node.snap__name))

                results = run_assessment(root.devices.device[kp_node.snap__name],
                                         root.ncs__services.snap__health_samples.snap__setup.snap__stop_on_error,
                                         root.ncs__services.snap__health_samples.snap__assessment[assessment_name],
                                         self.log)

            # Figure out the outcome
            assessment_outcome = all(map(lambda entry: entry[2], results))
            if assessment_outcome:
                output.success = "Assessment completed without errors"
            else:
                failed_cmds = ', '.join([str(seq) for seq, result, passed in results if not passed])
                output.failure = "Assessment completed with errors. Commands that failed: {}".format(failed_cmds)

        except AssessmentError as e:
            output.failure = "Assessment error: {}".format(e)
            self.log.info("Lightweight assessment error: {}".format(e))


class ClearAssessments(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info("Action {}".format(name))
        with ncs.maapi.single_write_trans(uinfo.username, "system", db=ncs.OPERATIONAL) as write_t:
            root = ncs.maagic.get_root(write_t)
            kp_node = ncs.maagic.cd(root, kp)
            del kp_node.snap__sample

            write_t.apply()

            output.success = "Cleared all assessments from device"


class DiffAssessments(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):

        def sample_cmds(sample_node):
            """
            Return commands in a sample as a generator
            :param sample_node: maagic node representing a sample instance
            :return: list of (seq, output, passed) tuples
            """
            return ((cmd.seq, cmd.output, cmd.passed) for cmd in
                    sorted(sample_node.command, key=lambda cmd_entry: cmd_entry.seq))

        self.log.info("Action {}".format(name))
        action_set_timeout(uinfo, 240)

        with ncs.maapi.single_read_trans(uinfo.username, "system") as read_t:
            root = ncs.maagic.get_root(read_t)
            # kp: /ncs:services/snap:health-samples/device{IOS-0}
            kp_node = ncs.maagic.cd(root, kp)

            ts_list = sorted([sample_entry.timestamp for sample_entry in kp_node.snap__sample])

            try:
                if len(ts_list) < 2:
                    raise DiffFailed("Not enough samples to complete")

                assessment = root.ncs__services.snap__health_samples.snap__assessment[kp_node.snap__assessment]
                parse_dict = {cmd.seq: cmd.parse for cmd in assessment.snap__command}

                last_ts, prev_ts = ts_list[-1], ts_list[-2]
                self.log.info("{}: comparing {} and {} samples".format(kp_node.snap__name, last_ts, prev_ts))

                # Iterate over commands from the last 2 samples
                zipped_cmds = zip(sample_cmds(kp_node.snap__sample[last_ts]), sample_cmds(kp_node.snap__sample[prev_ts]))
                for (last_seq, last_output, last_passed), (prev_seq, prev_output, prev_passed) in zipped_cmds:
                    if last_seq != prev_seq:
                        raise DiffFailed("Command sequence number mismatch: last: {}, prev: {}".format(last_seq, prev_seq))
                    if not last_passed:
                        raise DiffFailed("Last assessment failed: {}".format(last_ts))
                    if not prev_passed:
                        raise DiffFailed("Previous assessment failed: {}".format(prev_ts))

                    cmd_run = assessment.snap__command[last_seq].snap__run
                    self.log.info("Comparing '{}' ({}) output".format(cmd_run, last_seq))

                    last_tokens = cmd_parse(last_output, parse_dict[last_seq])
                    prev_tokens = cmd_parse(prev_output, parse_dict[prev_seq])
                    if len(last_tokens) != len(prev_tokens):
                        raise DiffFailed("Different number of matches across samples from '{}' ({})".format(cmd_run, last_seq))

                    token_match = [(token_last == token_prev) for token_last, token_prev in zip(last_tokens, prev_tokens)]
                    if not all(token_match):
                        raise DiffFailed("Output from '{}' ({}) changed".format(cmd_run, last_seq))

                output.success = "Diff passed"
                self.log.info("Diff assessment passed")

            except DiffFailed as e:
                output.failure = "Diff failed: {}".format(e)
                self.log.info("Diff assessment failed: {}".format(e))


class AssessmentError(Exception):
    """ Exception indicating error while running an assessment """
    pass


class DiffFailed(Exception):
    """ Exception indicating error in assessment diff """
    pass


# ---------------------------------------------
# UTILS
# ---------------------------------------------
def live_status_any(device):
    # Mapping of ned-id to exec namespaces
    stats_exec = {
        'cisco-ios': 'ios_stats__exec',
        'cisco-nx': 'nx_stats__exec',
        'cisco-ios-xr': 'cisco_ios_xr_stats__exec',
    }
    ned = device.device_type.cli.ned_id.split(':')[-1]
    return getattr(device.live_status, stats_exec[ned]).any


def cmd_parse(command_output, parse_regex):
    return re.findall(parse_regex, command_output, re.MULTILINE)


def run_assessment(device_node, stop_on_error, assessment_node, logger):
    def cmd_evaluate(cmd_output, parse_regex, expect_regex):
        token_list = cmd_parse(cmd_output, parse_regex)
        return len(token_list) > 0 and all(
            map(lambda parsed_item: re.match(expect_regex, parsed_item, re.MULTILINE) is not None, token_list)
        )

    outcome_list = []
    cmd_list = [(cmd.seq, cmd.run, cmd.parse, cmd.expect) for cmd in assessment_node.snap__command]
    for seq, run, parse, expect in sorted(cmd_list, key=lambda cmd_entry: cmd_entry[0]):
        exec_any = live_status_any(device_node)
        exec_any_input = exec_any.get_input()
        exec_any_input.args = [run]

        result = exec_any(exec_any_input).result
        passed = expect is None or cmd_evaluate(result, parse, expect)

        outcome_list.append(
            (seq, result, passed)
        )

        logger.info("{}, '{}' ({}), pass: {}".format(device_node.name, run, seq, passed))

        if not passed and stop_on_error:
            break

    if len(outcome_list) < 1:
        raise AssessmentError("Assessment failed to run")

    return outcome_list


# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        self.log.info('Main RUNNING')

        # Registration of action callbacks
        self.register_action('run-assessment', RunAssessment)
        self.register_action('run-light-assessment', RunLightAssessment)
        self.register_action('clear-assessments', ClearAssessments)
        self.register_action('diff-assessments', DiffAssessments)

    def teardown(self):
        self.log.info('Main FINISHED')
