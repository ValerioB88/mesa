"""
ModularServer
=============

A visualization server which renders a model via one or more elements.

The concept for the modular visualization server as follows:
A visualization is composed of VisualizationElements, each of which defines how
to generate some visualization from a model instance and render it on the
client. VisualizationElements may be anything from a simple text display to
a multilayered HTML5 canvas.

The actual server is launched with one or more VisualizationElements;
it runs the model object through each of them, generating data to be sent to
the client. The client page is also generated based on the JavaScript code
provided by each element.

This file consists of the following classes:

VisualizationElement: Parent class for all other visualization elements, with
                      the minimal necessary options.
PageHandler: The handler for the visualization page, generated from a template
             and built from the various visualization elements.
SocketHandler: Handles the websocket connection between the client page and
                the server.
ModularServer: The overall visualization application class which stores and
               controls the model and visualization instance.


ModularServer should *not* need to be subclassed on a model-by-model basis; it
should be primarily a pass-through for VisualizationElement subclasses, which
define the actual visualization specifics.

For example, suppose we have created two visualization elements for our model,
called canvasvis and graphvis; we would launch a server with:

    server = ModularServer(MyModel, [canvasvis, graphvis], name="My Model")
    server.launch()

The client keeps track of what step it is showing. Clicking the Step button in
the browser sends a message requesting the viz_state corresponding to the next
step position, which is then sent back to the client via the websocket.

The websocket protocol is as follows:
Each message is a JSON object, with a "type" property which defines the rest of
the structure.

Server -> Client:
    Send over the model state to visualize.
    Model state is a list, with each element corresponding to a div; each div
    is expected to have a render function associated with it, which knows how
    to render that particular data. The example below includes two elements:
    the first is data for a CanvasGrid, the second for a raw text display.

    {
    "type": "viz_state",
    "data": [{0:[ {"Shape": "circle", "x": 0, "y": 0, "r": 0.5,
                "Color": "#AAAAAA", "Filled": "true", "Layer": 0,
                "text": 'A', "text_color": "white" }]},
            "Shape Count: 1"]
    }

    Informs the client that the model is over.
    {"type": "end"}

    Informs the client of the current model's parameters
    {
    "type": "model_params",
    "params": 'dict' of model params, (i.e. {arg_1: val_1, ...})
    }

Client -> Server:
    Reset the model.
    TODO: Allow this to come with parameters
    {
    "type": "reset"
    }

    Get a given state.
    {
    "type": "get_step",
    "step:" index of the step to get.
    }

    Submit model parameter updates
    {
    "type": "submit_params",
    "param": name of model parameter
    "value": new value for 'param'
    }

    Get the model's parameters
    {
    "type": "get_params"
    }

"""
import asyncio
import os
import platform
import tornado.autoreload
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.escape
import tornado.gen
import webbrowser
from copy import deepcopy

from mesa.visualization.UserParam import UserSettableParameter

# Suppress several pylint warnings for this file.
# Attributes being defined outside of init is a Tornado feature.
# pylint: disable=attribute-defined-outside-init

# Change the event loop policy for windows
if platform.system() == "Windows" and platform.python_version_tuple() >= ("3", "7"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SERVER = None
class VisualizationElement:
    """
    Defines an element of the visualization.

    Attributes:
        package_includes: A list of external JavaScript files to include that
                          are part of the Mesa packages.
        local_includes: A list of JavaScript files that are local to the
                        directory that the server is being run in.
        js_code: A JavaScript code string to instantiate the element.

    Methods:
        render: Takes a model object, and produces JSON data which can be sent
                to the client.

    """

    package_includes = []
    local_includes = []
    js_code = ""
    render_args = {}

    def __init__(self):
        pass

    def render(self, model):
        """Build visualization data from a model object.

        Args:
            model: A model object

        Returns:
            A JSON-ready object.

        """
        return "<b>VisualizationElement goes here</b>."


# =============================================================================
# Actual Tornado code starts here:


class PageHandler(tornado.web.RequestHandler):
    """Handler for the HTML template which holds the visualization."""

    def get(self):
        elements = self.application.visualization_elements
        for i, element in enumerate(elements):
            element.index = i
        self.render(
            "modular_template.html",
            port=self.application.port,
            model_name=self.application.model_name,
            description=self.application.description,
            package_includes=self.application.package_includes,
            local_includes=self.application.local_includes,
            scripts=self.application.js_code,
        )


class SocketHandler(tornado.websocket.WebSocketHandler):
    """Handler for websocket."""

    def open(self):
        if self.application.verbose:
            print("Socket opened!")
        self.write_message(
            {"type": "model_params", "params": self.application.user_params}
        )

    def check_origin(self, origin):
        return True

    @property
    def viz_state_message(self):
        return {"type": "viz_state", "data": self.application.render_model()}

    def on_message(self, message):
        """Receiving a message from the websocket, parse, and act accordingly."""
        if self.application.verbose:
            print(message)
        msg = tornado.escape.json_decode(message)

        if msg["type"] == "get_step":
            if not self.application.model.running:
                self.write_message({"type": "end"})
            else:
                self.application.model.step()
                self.write_message(self.viz_state_message)

        elif msg["type"] == "reset":
            self.application.reset_model()
            self.write_message(self.viz_state_message)

        elif msg["type"] == "close-socket":
            self.application.model.stop()

        elif msg["type"] == "model_attrib":
            print("Value Received")
            self.application.model.__setattr__(msg["param"], eval(msg["value"]))
        elif msg["type"] == "command":
            m = self.application.model
            agents = m.schedule.agents
            def find_pop_idx_from_id():
                for idx, a in enumerate(agents):
                    if a.unique_id == m.selected_agent_unique_id:
                        break
                return idx
            if msg["command"] == "next":
                idx = find_pop_idx_from_id()
                idx = 0 if idx+1 > len(agents) - 1 else idx+1
                m.selected_agent_unique_id = agents[idx].unique_id
            if msg["command"] == "previous":
                idx = find_pop_idx_from_id()
                idx = len(agents)-1 if idx+-1 < 0 else idx-1
                m.selected_agent_unique_id = agents[idx].unique_id
            if msg["command"] == "kill":
                idx = find_pop_idx_from_id()
                agents[idx].die()
            if msg["command"] == "offspring":
                idx = find_pop_idx_from_id()
                agents[idx].reproduce_asexual()
            if msg["command"] == "save":
                self.application.model.save_model_state()


        elif msg["type"] == "submit_params":
            param = msg["param"]
            value = msg["value"]

            # Is the param editable?
            if param in self.application.user_params:
                if isinstance(
                    self.application.model_kwargs[param], UserSettableParameter
                ):
                    self.application.model_kwargs[param].value = value
                else:
                    self.application.model_kwargs[param] = value

        else:
            if self.application.verbose:
                print("Unexpected message!")

from tornado.web import HTTPError
class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        # Disable cache
        self.set_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')

class ImageStaticFileHandler(tornado.web.StaticFileHandler):
    def initialize(self, model, path: str, default_filename = None) -> None:
        self.root = path
        self.default_filename = default_filename
        self.model = model

    def validate_absolute_path(self, root: str, absolute_path: str):
        try:
            return super().validate_absolute_path(root, absolute_path)
        except tornado.web.HTTPError:
            import re
            import evoagent.visualize as vis
            id = int(re.findall(r'id(\d+).svg', absolute_path)[0])
            vis.draw_net(self.model.config, self.model.schedule._agents[id].genome, view=False, filename=self.model.net_svg_folder + f'id{self.model.schedule._agents[id].unique_id}')
            return super().validate_absolute_path(root, absolute_path)



class ModularServer(tornado.web.Application):
    """Main visualization application."""

    verbose = True
    event_loop = None
    port = int(os.getenv("PORT", 8521))  # Default port to listen on
    max_steps = 100000

    # Handlers and other globals:
    page_handler = (r"/", PageHandler)
    socket_handler = (r"/ws", SocketHandler)
    static_handler = (
        r"/static/(.*)",
        NoCacheStaticFileHandler,
        {"path": os.path.dirname(__file__) + "/templates"},
    )
    local_handler = (r"/local/(.*)", NoCacheStaticFileHandler, {"path": ""})

    handlers = [page_handler, socket_handler, static_handler, local_handler]

    settings = {
        "debug": False,
        "autoreload": False,
        "template_path": os.path.dirname(__file__) + "/templates",
    }

    EXCLUDE_LIST = ("width", "height")

    def __init__(
        self, model_cls, visualization_elements, name="Mesa Model", model_params=None, verbose=True
    ):
        if not model_params:
            model_params = {}
        """Create a new visualization server with the given elements."""
        # Prep visualization elements:
        self.visualization_elements = visualization_elements
        self.package_includes = set()
        self.local_includes = set()
        self.js_code = []
        for element in self.visualization_elements:
            for include_file in element.package_includes:
                self.package_includes.add(include_file)
            for include_file in element.local_includes:
                self.local_includes.add(include_file)
            self.js_code.append(element.js_code)

        # Initializing the model
        self.model_name = name
        self.model_cls = model_cls
        self.description = "No description available"
        if hasattr(model_cls, "description"):
            self.description = model_cls.description
        elif model_cls.__doc__ is not None:
            self.description = model_cls.__doc__
        self.handlers = [self.page_handler, self.socket_handler, self.static_handler, self.local_handler]

        self.model_kwargs = model_params
        self.reset_model()

        self.svg_image_handler = (
            r"/images/(.*)",
            ImageStaticFileHandler,
            {"model": self.model, "path": f'./{self.model.net_svg_folder}'}
        )
        self.handlers.append(self.svg_image_handler)
        # Initializing the application itself:
        super().__init__(self.handlers, **self.settings)
        self.verbose = verbose

    @property
    def user_params(self):
        result = {}
        for param, val in self.model_kwargs.items():
            if isinstance(val, UserSettableParameter):
                result[param] = val.json

        return result

    def reset_model(self):
        """Reinstantiate the model object, using the current parameters."""

        model_params = {}
        for key, val in self.model_kwargs.items():
            if isinstance(val, UserSettableParameter):
                if (
                    val.param_type == "static_text"
                ):  # static_text is never used for setting params
                    continue
                model_params[key] = val.value
            else:
                model_params[key] = val

        self.model = self.model_cls(**deepcopy(model_params), server_model=self)

    def render_model(self):
        """Turn the current state of the model into a dictionary of
        visualizations

        """
        visualization_state = []
        for element in self.visualization_elements:
            element_state = element.render(self.model)
            visualization_state.append(element_state)
        return visualization_state

    def launch(self, port=8500, open_browser=True):
        """Run the app."""
        if port is not None:
            self.port = port
        repeat = True
        while repeat:
            try:
                self.listen(self.port)
                repeat = False
            except OSError:
                self.port += 1
        print(f"Using port {self.port}")
        url = f"http://127.0.0.1:{self.port}"
        print(f"Interface starting at {url}")
        self.event_loop = tornado.ioloop.IOLoop.current()
        if open_browser:
            webbrowser.open(url)
        # tornado.autoreload.start()
        self.event_loop.start()
