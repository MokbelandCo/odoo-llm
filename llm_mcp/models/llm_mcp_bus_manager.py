import json
import logging
import os
import select
import shlex
import subprocess
import threading
import time

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Prefer a current MCP protocol version so modern stdio servers negotiate.
# 16.0 sent "0.1.0", which current MCP SDKs reject.
MCP_CLIENT_PROTOCOL_VERSION = "2025-03-26"
MCP_CLIENT_INFO = {"name": "odoo-llm-mcp", "version": "19.0.1.0.0"}


class MCPBusManager:
    """
    Manager for MCP server communication over stdio JSON-RPC 2.0.

    Process-wide singleton keyed by Odoo ``llm.mcp.server`` id. Direct pipe
    I/O is the working path from the 16.0 module (the bus bridge is optional).
    """

    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, env, server_id, command=None, args=None):
        key = f"server_{server_id}"

        with cls._lock:
            if key not in cls._instances:
                instance = super().__new__(cls)
                instance._init_properties(env, server_id, command, args)
                cls._instances[key] = instance
            else:
                instance = cls._instances[key]
                if command and (
                    instance.command != command or (instance.args or "") != (args or "")
                ):
                    instance.close()
                    instance._init_properties(env, server_id, command, args)
            return cls._instances[key]

    @classmethod
    def discard(cls, server_id):
        """Stop and drop the singleton for ``server_id`` if it exists."""
        key = f"server_{server_id}"
        with cls._lock:
            instance = cls._instances.pop(key, None)
        if instance:
            instance.close()
        return True

    def _init_properties(self, env, server_id, command, args):
        self.env = env
        self.server_id = server_id
        self.command = command
        self.args = args
        self._initialized = False
        self._request_counter = 0
        self._pending_requests = {}
        self.protocol_version = None
        self.server_info = None

        self.process = None
        self.process_thread = None
        self.stop_event = threading.Event()

        self._response_event = threading.Event()
        self._responses = {}

    def _start_process(self):
        """Start the MCP server process directly"""
        if self.process and self.process.poll() is None:
            _logger.info("MCP process for server %s is already running", self.server_id)
            return True

        try:
            full_command = self.command
            if self.args:
                full_command = f"{full_command} {self.args}"

            cmd = shlex.split(full_command)
            _logger.info("Starting MCP process with command: %s", cmd)

            child_env = os.environ.copy()
            child_env.setdefault("PYTHONUNBUFFERED", "1")

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=child_env,
            )

            if self.process.poll() is not None:
                stderr_output = "N/A"
                try:
                    stderr_output = self.process.stderr.read()
                except Exception:
                    pass
                _logger.error(
                    "Process exited immediately with code %s and error: %s",
                    self.process.returncode,
                    stderr_output,
                )
                return False

            self.stop_event.clear()
            self.process_thread = threading.Thread(
                target=self._process_reader_loop, name=f"mcp-reader-{self.server_id}"
            )
            self.process_thread.daemon = True
            self.process_thread.start()

            error_thread = threading.Thread(
                target=self._process_error_reader_loop,
                name=f"mcp-error-{self.server_id}",
            )
            error_thread.daemon = True
            error_thread.start()

            _logger.info(
                "MCP process started successfully for server %s", self.server_id
            )
            return True

        except Exception as e:
            _logger.error(
                "Failed to start MCP process for server %s: %s", self.server_id, e
            )
            return False

    def _stop_process(self):
        """Stop the MCP server process"""
        if not self.process:
            return True

        try:
            self.stop_event.set()

            if self.process.poll() is None:
                _logger.info("Terminating MCP process for server %s", self.server_id)
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _logger.warning(
                        "MCP process for server %s did not terminate, killing it",
                        self.server_id,
                    )
                    self.process.kill()
                    self.process.wait(timeout=2)

            if self.process_thread and self.process_thread.is_alive():
                self.process_thread.join(timeout=5)

            self.process = None
            self.process_thread = None
            self._initialized = False
            return True
        except Exception as e:
            _logger.error(
                "Error stopping MCP process for server %s: %s", self.server_id, e
            )
            return False

    def _process_reader_loop(self):
        """Reader thread to process stdout from the MCP server"""
        _logger.info("Started reader thread for MCP server %s", self.server_id)

        try:
            while not self.stop_event.is_set():
                if not self.process or self.process.poll() is not None:
                    _logger.warning(
                        "MCP process for server %s has exited, reader thread stopping",
                        self.server_id,
                    )
                    break

                ready_to_read, _, _ = select.select([self.process.stdout], [], [], 0.1)
                if not ready_to_read:
                    continue

                line = self.process.stdout.readline().strip()
                if not line:
                    continue

                _logger.debug("Read from MCP server %s: %s", self.server_id, line)

                try:
                    response = json.loads(line)
                    if isinstance(response, dict) and "id" in response:
                        request_id = response["id"]
                        _logger.info(
                            "Received response with id %s from MCP server %s",
                            request_id,
                            self.server_id,
                        )
                        self._responses[request_id] = response
                        self._response_event.set()
                    else:
                        _logger.warning(
                            "Received unexpected response format from MCP server %s: %s",
                            self.server_id,
                            response,
                        )
                except json.JSONDecodeError as e:
                    _logger.warning(
                        "Invalid JSON from MCP server %s: %s, data: %s",
                        self.server_id,
                        e,
                        line,
                    )
                except Exception as e:
                    _logger.error(
                        "Error processing response from MCP server %s: %s",
                        self.server_id,
                        e,
                    )

        except Exception as e:
            if not self.stop_event.is_set():
                _logger.error(
                    "Error in reader thread for MCP server %s: %s", self.server_id, e
                )

        _logger.info("Reader thread for MCP server %s exiting", self.server_id)

    def _process_error_reader_loop(self):
        """Reader thread to process stderr from the MCP server"""
        try:
            while not self.stop_event.is_set():
                if not self.process or self.process.poll() is not None:
                    break

                ready_to_read, _, _ = select.select([self.process.stderr], [], [], 0.1)
                if not ready_to_read:
                    continue

                line = self.process.stderr.readline().strip()
                if not line:
                    continue

                _logger.warning("MCP server %s stderr: %s", self.server_id, line)
        except Exception as e:
            if not self.stop_event.is_set():
                _logger.error(
                    "Error reading stderr from MCP server %s: %s", self.server_id, e
                )

    def _send_message(self, message):
        """Send a message to the MCP server process"""
        if not self.process or self.process.poll() is not None:
            if not self._start_process():
                raise UserError(
                    f"Failed to start MCP server process for server {self.server_id}"
                )

        try:
            if "id" not in message and message.get("method", "").startswith(
                "notifications/"
            ):
                json_str = json.dumps(message)
                _logger.info("Sending to MCP server %s: %s", self.server_id, json_str)
                self.process.stdin.write(f"{json_str}\n")
                self.process.stdin.flush()
                return None

            if "id" not in message:
                message["id"] = self._get_next_request_id()

            request_id = message["id"]
            self._pending_requests[request_id] = {
                "timestamp": time.time(),
                "message": message,
            }

            self._response_event.clear()

            json_str = json.dumps(message)
            _logger.info("Sending to MCP server %s: %s", self.server_id, json_str)
            self.process.stdin.write(f"{json_str}\n")
            self.process.stdin.flush()

            return request_id
        except Exception as e:
            _logger.error(
                "Error sending message to MCP server %s: %s", self.server_id, e
            )
            raise UserError(f"Failed to communicate with MCP server: {e}") from e

    def _get_next_request_id(self):
        with self._lock:
            self._request_counter += 1
            return self._request_counter

    def _wait_for_response(self, request_id, timeout=30):
        """Wait for a response from the MCP server"""
        _logger.info(
            "Waiting for response to request %s from MCP server %s",
            request_id,
            self.server_id,
        )
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.process and self.process.poll() is not None:
                exit_code = self.process.returncode
                stderr_output = "N/A"
                try:
                    stderr_output = "".join(self.process.stderr.readlines())
                except Exception:
                    pass
                _logger.error(
                    "MCP server %s process exited with code %s while waiting for "
                    "response %s. Error: %s",
                    self.server_id,
                    exit_code,
                    request_id,
                    stderr_output,
                )
                return None

            if request_id in self._responses:
                response = self._responses.pop(request_id)
                if request_id in self._pending_requests:
                    del self._pending_requests[request_id]
                _logger.info(
                    "Received response for request %s from MCP server %s",
                    request_id,
                    self.server_id,
                )
                return response

            self._response_event.wait(0.1)
            self._response_event.clear()

        _logger.error("Timeout waiting for response to request %s", request_id)

        if request_id in self._pending_requests:
            del self._pending_requests[request_id]

        return None

    def _initialize_mcp(self):
        """Initialize the MCP protocol with the server"""
        if self._initialized:
            _logger.info("MCP protocol already initialized")
            return True

        try:
            if not self._start_process():
                _logger.error(
                    "Failed to start process for MCP server %s", self.server_id
                )
                return False

            initialize_request = {
                "jsonrpc": "2.0",
                "id": self._get_next_request_id(),
                "method": "initialize",
                "params": {
                    "clientInfo": MCP_CLIENT_INFO,
                    "protocolVersion": MCP_CLIENT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                },
            }

            request_id = initialize_request["id"]
            _logger.info("Initializing MCP protocol with request id %s", request_id)

            self._send_message(initialize_request)
            response = self._wait_for_response(request_id, timeout=15)

            if response is None:
                _logger.error("No response received for MCP initialization")
                return False

            if "result" in response:
                self._initialized = True

                if "protocolVersion" in response["result"]:
                    self.protocol_version = response["result"]["protocolVersion"]
                if "serverInfo" in response["result"]:
                    self.server_info = response["result"]["serverInfo"]

                initialized_notification = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
                _logger.info("Sending initialized notification")
                self._send_message(initialized_notification)

                _logger.info(
                    "MCP server initialized successfully with protocol version %s",
                    self.protocol_version,
                )
                return True

            error_message = "Unknown error"
            if "error" in response:
                error_message = response["error"].get("message", "Unknown error")
            _logger.error("Failed to initialize MCP server: %s", error_message)
            return False

        except Exception as e:
            _logger.error("Error initializing MCP server: %s", e)
            return False

    def list_tools(self):
        """Send a tools/list request to the server"""
        if not self._initialized and not self._initialize_mcp():
            _logger.error("Failed to initialize MCP before listing tools")
            return None

        try:
            request_id = self._get_next_request_id()
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/list",
                "params": {},
            }

            _logger.info("Sending tools/list request with id %s", request_id)
            self._send_message(request)

            response = self._wait_for_response(request_id)

            if response is None:
                _logger.error("No response received for tools/list request")
                return None

            if "result" in response and "tools" in response["result"]:
                _logger.info(
                    "Successfully listed %s tools from MCP server",
                    len(response["result"]["tools"]),
                )
                return response["result"]["tools"]

            error_message = "Unknown error"
            if "error" in response:
                error_message = response["error"].get("message", "Unknown error")
            _logger.error("Error listing tools: %s", error_message)
            return None

        except Exception as e:
            _logger.error("Exception listing tools: %s", e)
            return None

    def call_tool(self, tool_name, arguments):
        """Call a tool on the server"""
        if not self._initialized and not self._initialize_mcp():
            _logger.error("Failed to initialize MCP before calling tool %s", tool_name)
            return {"error": "Failed to initialize MCP connection"}

        try:
            request_id = self._get_next_request_id()
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }

            _logger.info(
                "Sending tools/call request for tool '%s' with id %s",
                tool_name,
                request_id,
            )
            self._send_message(request)

            response = self._wait_for_response(request_id, timeout=30)

            if response is None:
                _logger.error(
                    "No response received for tools/call request for tool '%s'",
                    tool_name,
                )
                return {"error": "No response from MCP server"}

            if "result" in response:
                result = response["result"]
                if result.get("isError"):
                    error_content = ""
                    if "content" in result:
                        for content_item in result["content"]:
                            if content_item.get("type") == "text":
                                error_content += content_item.get("text", "")
                    _logger.error(
                        "Tool '%s' execution failed: %s", tool_name, error_content
                    )
                    return {"error": error_content or "Tool execution failed"}

                content_result = {}
                if "content" in result:
                    for content_item in result["content"]:
                        if content_item.get("type") == "text":
                            text_content = content_item.get("text", "")
                            try:
                                content_result = json.loads(text_content)
                            except json.JSONDecodeError:
                                content_result = {"result": text_content}
                _logger.info("Tool '%s' execution succeeded", tool_name)
                return content_result

            error_message = "Unknown error"
            if "error" in response:
                error_message = response["error"].get("message", "Unknown error")
            _logger.error("Error calling tool %s: %s", tool_name, error_message)
            return {"error": error_message}

        except Exception as e:
            _logger.error("Exception calling tool %s: %s", tool_name, e)
            return {"error": str(e)}

    def close(self):
        """Close the MCP server connection"""
        return self._stop_process()
