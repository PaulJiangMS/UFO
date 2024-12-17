# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.


import json
from typing import TYPE_CHECKING

from pywinauto.controls.uiawrapper import UIAWrapper

from ufo import utils
from ufo.agents.processors.basic import BaseProcessor
from ufo.config.config import Config
from ufo.module.context import Context, ContextNames

configs = Config.get_instance().config_data
if configs is not None:
    BACKEND = configs["CONTROL_BACKEND"]

if TYPE_CHECKING:
    from ufo.agents.agent.host_agent import HostAgent


class HostAgentProcessor(BaseProcessor):
    """
    The processor for the host agent at a single step.
    """

    def __init__(self, agent: "HostAgent", context: Context) -> None:
        """
        Initialize the host agent processor.
        :param agent: The host agent to be processed.
        :param context: The context.
        """

        super().__init__(agent=agent, context=context)

        self.host_agent = agent

        self._desktop_screen_url = None
        self._desktop_windows_dict = None
        self._desktop_windows_info = None

    def print_step_info(self) -> None:
        """
        Print the step information.
        """
        utils.print_with_color(
            "Round {round_num}, Step {step}, HostAgent: Analyzing the user intent and decomposing the request...".format(
                round_num=self.round_num + 1, step=self.round_step + 1
            ),
            "magenta",
        )

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def capture_screenshot(self) -> None:
        """
        Capture the screenshot.
        """

        desktop_save_path = self.log_path + f"action_step{self.session_step}.png"

        self._memory_data.add_values_from_dict({"CleanScreenshot": desktop_save_path})

        # Capture the desktop screenshot for all screens.
        self.photographer.capture_desktop_screen_screenshot(
            all_screens=True, save_path=desktop_save_path
        )

        # Encode the desktop screenshot into base64 format as required by the LLM.
        self._desktop_screen_url = self.photographer.encode_image_from_path(
            desktop_save_path
        )

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def get_control_info(self) -> None:
        """
        Get the control information.
        """

        # Get all available windows on the desktop, into a dictionary with format {index: application object}.
        self._desktop_windows_dict = self.control_inspector.get_desktop_app_dict(
            remove_empty=True
        )

        # Get the textual information of all windows.
        self._desktop_windows_info = self.control_inspector.get_desktop_app_info(
            self._desktop_windows_dict
        )

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def get_prompt_message(self) -> None:
        """
        Get the prompt message.
        """

        # Construct the prompt message for the host agent.
        self._prompt_message = self.host_agent.message_constructor(
            image_list=[self._desktop_screen_url],
            os_info=self._desktop_windows_info,
            plan=self.prev_plan,
            prev_subtask=self.previous_subtasks,
            request=self.request,
        )

        # Log the prompt message. Only save them in debug mode.
        log = json.dumps(
            {
                "step": self.session_step,
                "prompt": self._prompt_message,
                "control_items": self._desktop_windows_info,
                "filted_control_items": self._desktop_windows_info,
                "status": "",
            }
        )
        self.request_logger.debug(log)

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def get_response(self) -> None:
        """
        Get the response from the LLM.
        """

        # Try to get the response from the LLM. If an error occurs, catch the exception and log the error.
        self._response, self.cost = self.host_agent.get_response(
            self._prompt_message, "HOSTAGENT", use_backup_engine=True
        )

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def parse_response(self) -> None:
        """
        Parse the response.
        """

        self._response_json = self.host_agent.response_to_dict(self._response)

        self.control_label = self._response_json.get("ControlLabel", "")
        self.control_text = self._response_json.get("ControlText", "")
        self.subtask = self._response_json.get("CurrentSubtask", "")
        self.host_message = self._response_json.get("Message", [])

        # Convert the plan from a string to a list if the plan is a string.
        self.plan = self.string2list(self._response_json.get("Plan", ""))
        self._response_json["Plan"] = self.plan

        self.status = self._response_json.get("Status", "")
        self.question_list = self._response_json.get("Questions", [])
        self.bash_command = self._response_json.get("Bash", None)

        self.host_agent.print_response(self._response_json)

    @BaseProcessor.exception_capture
    @BaseProcessor.method_timer
    def execute_action(self) -> None:
        """
        Execute the action.
        """

        new_app_window = self._desktop_windows_dict.get(self.control_label, None)

        # If the new application window is available, select the application.
        if new_app_window is not None:
            self._select_application(new_app_window)

        # If the bash command is not empty, run the shell command.
        if self.bash_command:
            self._run_shell_command()

        # If the new application window is None and the bash command is None, set the status to FINISH.
        if new_app_window is None and self.bash_command is None:
            self.status = self._agent_status_manager.FINISH.value
            return

    def _is_window_interface_available(self, new_app_window: UIAWrapper) -> bool:
        """
        Check if the window interface is available for the visual element.
        :param new_app_window: The new application window.
        :return: True if the window interface is available, False otherwise.
        """
        try:
            new_app_window.is_normal()
            return True
        except Exception:
            utils.print_with_color(
                "Window interface {title} not available for the visual element.".format(
                    title=self.control_text
                ),
                "red",
            )
            return False

    def _is_same_window(self, window1: UIAWrapper, window2: UIAWrapper) -> bool:
        """
        Check if two windows are the same.
        :param window1: The first window.
        :param window2: The second window.
        :return: True if the two windows are the same, False otherwise.
        """

        equal = False

        try:
            equal = window1 == window2
        except:
            pass
        return equal

    def _switch_to_new_app_window(self, new_app_window: UIAWrapper) -> None:
        """
        Switch to the new application window if it is different from the current application window.
        :param new_app_window: The new application window.
        """

        if (
            not self._is_same_window(new_app_window, self.application_window)
            and self.application_window is not None
        ):
            utils.print_with_color("Switching to a new application...", "magenta")

        self.application_window = new_app_window

        self.context.set(ContextNames.APPLICATION_WINDOW, self.application_window)
        self.context.set(ContextNames.APPLICATION_ROOT_NAME, self.app_root)
        self.context.set(ContextNames.APPLICATION_PROCESS_NAME, self.control_text)

    def _select_application(self, application_window: UIAWrapper) -> None:
        """
        Create the app agent for the host agent.
        :param application_window: The application window.
        """

        self._control_log = {
            "control_class": application_window.element_info.class_name,
            "control_type": application_window.element_info.control_type,
            "control_automation_id": application_window.element_info.automation_id,
        }

        # Get the root name of the application.
        self.app_root = self.control_inspector.get_application_root_name(
            application_window
        )

        # Check if the window interface is available for the visual element.
        if not self._is_window_interface_available(application_window):
            self.status = self._agent_status_manager.ERROR.value

            return

        # Switch to the new application window, if it is different from the current application window.
        self._switch_to_new_app_window(application_window)
        self.application_window.set_focus()
        if configs.get("SHOW_VISUAL_OUTLINE_ON_SCREEN", True):
            self.application_window.draw_outline(colour="red", thickness=3)

        self.action = "set_focus()"

    def _run_shell_command(self) -> None:
        """
        Run the shell command.
        """
        self.agent.create_puppeteer_interface()
        self.agent.Puppeteer.receiver_manager.create_api_receiver(
            self.app_root, self.control_text
        )

        self._results = self.agent.Puppeteer.execute_command(
            "run_shell", {"command": self.bash_command}
        )

        self.action = self.agent.Puppeteer.get_command_string(
            "run_shell", {"command": self.bash_command}
        )

    def sync_memory(self):
        """
        Sync the memory of the HostAgent.
        """
        additional_memory = {
            "Step": self.session_step,
            "RoundStep": self.round_step,
            "AgentStep": self.host_agent.step,
            "Round": self.round_num,
            "ControlLabel": self.control_label,
            "SubtaskIndex": -1,
            "Action": self.action,
            "ActionType": "UIControl",
            "Request": self.request,
            "Agent": "HostAgent",
            "AgentName": self.host_agent.name,
            "Application": self.app_root,
            "Cost": self._cost,
            "Results": self._results,
            "error": self._exeception_traceback,
        }

        self.add_to_memory(self._response_json)
        self.add_to_memory(additional_memory)
        self.add_to_memory(self._control_log)
        self.add_to_memory({"time_cost": self._time_cost})

    def update_memory(self) -> None:
        """
        Update the memory of the Agent.
        """

        # Sync the memory
        self.sync_memory()

        self.host_agent.add_memory(self._memory_data)

        # Log the memory item.
        self.context.add_to_structural_logs(self._memory_data.to_dict())
        # self.log(self._memory_data.to_dict())

        # Only memorize the keys in the HISTORY_KEYS list to feed into the prompt message in the future steps.
        memorized_action = {
            key: self._memory_data.to_dict().get(key) for key in configs["HISTORY_KEYS"]
        }

        self.host_agent.blackboard.add_trajectories(memorized_action)