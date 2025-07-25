"""Contains the code responsible for model visualization."""

from collections.abc import Sequence
import stormvogel.model
import stormvogel.layout
import stormvogel.result
import stormvogel.network
import stormvogel.displayable
from stormvogel.autoscale_svg import autoscale_svg
from .graph import ModelGraph, NodeType
from . import simulator

import networkx as nx
import matplotlib.pyplot as plt
from numpy.typing import NDArray

import logging
import json
import html
import ipywidgets as widgets
import IPython.display as ipd
import random
import string
from fractions import Fraction


def und(x: str) -> str:
    """Replace spaces by underscores."""
    return x.replace(" ", "_")


def random_word(k: int) -> str:
    """Random word of lenght k"""
    return "".join(random.choices(string.ascii_letters, k=k))


def random_color() -> str:
    """Return a random HEX color."""
    return "#" + "".join([random.choice("0123456789ABCDEF") for j in range(6)])


def blend_colors(c1: str, c2: str, factor: float) -> str:
    """Blend two colors in HEX format. #RRGGBB.
    Args:
        color1 (str): Color 1 in HEX format #RRGGBB
        color2 (str): Color 2 in HEX format #RRGGBB
        factor (float): The fraction of the resulting color that should come from color1."""
    r1 = int("0x" + c1[1:3], 0)
    g1 = int("0x" + c1[3:5], 0)
    b1 = int("0x" + c1[5:7], 0)
    r2 = int("0x" + c2[1:3], 0)
    g2 = int("0x" + c2[3:5], 0)
    b2 = int("0x" + c2[5:7], 0)
    r_res = int(factor * r1 + (1 - factor) * r2)
    g_res = int(factor * g1 + (1 - factor) * g2)
    b_res = int(factor * b1 + (1 - factor) * b2)
    return "#" + "".join("%02x" % i for i in [r_res, g_res, b_res])


class JSVisualization:
    """Handles visualization of a Model using a Network from stormvogel.network."""

    ACTION_ID_OFFSET: int = 10**10
    # In the visualization, both actions and states are nodes with an id.
    # This offset is used to keep their ids from colliding. It should be some high constant.

    def __init__(
        self,
        model: stormvogel.model.Model,
        result: stormvogel.result.Result | None = None,
        scheduler: stormvogel.result.Scheduler | None = None,
        layout: stormvogel.layout.Layout = stormvogel.layout.DEFAULT(),
        output: widgets.Output | None = None,
        debug_output: widgets.Output = widgets.Output(),
        use_iframe: bool = False,
        do_init_server: bool = True,
        do_display: bool = True,
        max_states: int = 1000,
        max_physics_states: int = 500,
        width: int = 800,
        height: int = 600,
        spam: widgets.Output = widgets.Output(),
    ) -> None:
        """Create and show a visualization of a Model using a visjs Network
        Args:
            model (Model): The stormvogel model to be displayed.
            result (Result, optional): A result associatied with the model.
                The results are displayed as numbers on a state. Enable the layout editor for options.
                If this result has a scheduler, then the scheduled actions will have a different color etc. based on the layout
            scheduler (Scheduler, optional): The scheduled actions will have a different color etc. based on the layout
                If both result and scheduler are set, then scheduler takes precedence.
            layout (Layout): Layout used for the visualization.
            show_editor (bool): Show an interactive layout editor.
            use_iframe (bool): Wrap the generated html inside of an IFrame.
                In some environments, the visualization works better with this enabled.
            output (widgets.Output): The output widget in which the network is rendered.
                Whether this widget is also displayed automatically depends on do_display.
            debug_output (widgets.Output): Output widget that can be used to debug interactive features.
        """
        if output is None:
            self.output = widgets.Output()
        else:
            self.output = output
        self.do_display: bool = do_display
        self.debug_output: widgets.Output = debug_output
        self.spam = spam
        with self.output:
            ipd.display(self.spam)

        # vis stuff
        self.name: str = random_word(10)
        self.model: stormvogel.model.Model = model
        self.result: stormvogel.result.Result | None = result
        self.scheduler: stormvogel.result.Scheduler | None = scheduler
        self.use_iframe: bool = use_iframe
        self.max_states: int = max_states
        self.max_physics_states: int = max_physics_states
        # If a scheduler was not set explictly, but a result was set, then take the scheduler from the results.
        self.layout: stormvogel.layout.Layout = layout
        if self.scheduler is None:
            if self.result is not None:
                self.scheduler = self.result.scheduler

        # Set "scheduler" as an active group iff it is present.
        if self.scheduler is not None:
            layout.add_active_group("scheduled_actions")
        else:  # Otherwise, disable it
            layout.remove_active_group("scheduled_actions")

        self.do_init_server: bool = do_init_server
        self.nt = ModelGraph.from_model(
            model,
            state_properties=self._create_state_properties,
            action_properties=self._create_action_properties,
            transition_properties=self._create_transition_properties,
        )
        self.network_wrapper: str = ""  # Use this for javascript injection.
        if self.use_iframe:
            self.network_wrapper: str = (
                f"document.getElementById('{self.name}').contentWindow.nw_{self.name}"
            )
        else:
            self.network_wrapper: str = f"nw_{self.name}"
        self.width: int = width
        self.height: int = height
        self.nodes_js: str = ""
        self.edges_js: str = ""
        self.options_js: str = "{}"
        self.new_nodes_hidden: bool = False
        if do_init_server:
            self.server: stormvogel.communication_server.CommunicationServer = (
                stormvogel.communication_server.initialize_server()
            )
        self.generate_js()

    # @classmethod
    # def from_model(cls, model: stormvogel.model.Model):
    #     G = ModelGraph.from_model(
    #             model,
    #             state_properties=self._create_state_properties,
    #             action_properties=self._create_action_properties,
    #             transition_properties=self._create_transition_properties,
    #             )

    def _create_state_properties(self, state: stormvogel.model.State):
        res = self.format_result(state)
        observations = self.__format_observations(state)
        rewards = self.__format_rewards(state, stormvogel.model.EmptyAction)
        group = self.__group_state(state, "states")
        id_label_part = (
            f"{state.id}\n"
            if self.layout.layout["state_properties"]["show_ids"]
            else ""
        )

        color = None

        result_colors = self.layout.layout["results"]["result_colors"]
        if result_colors and self.result is not None:
            result = self.result.get_result_of_state(state)
            max_result = self.result.maximum_result()
            if isinstance(result, (int, float, Fraction)) and isinstance(
                max_result, (int, float, Fraction)
            ):
                color1 = self.layout.layout["results"]["max_result_color"]
                color2 = self.layout.layout["results"]["min_result_color"]
                factor = result / max_result if max_result != 0 else 0
                color = blend_colors(color1, color2, float(factor))
        properties = {
            "label": id_label_part
            + ",".join(state.labels)
            + rewards
            + res
            + observations,
            "group": group,
            "color": color,
        }
        return properties

    def _create_action_properties(
        self, state: stormvogel.model.State, action: stormvogel.model.Action
    ) -> dict:
        group = self.__group_action(state.id, action, "actions")
        reward = self.__format_rewards(self.model.get_state_by_id(state.id), action)

        properties = {
            "label": ",".join(action.labels) + reward,
            "group": group,
        }
        return properties

    def _create_transition_properties(self, state, action, next_state) -> dict:
        properties = dict()
        transitions = state.get_outgoing_transitions(action)
        if transitions is None:
            return properties
        for prob, target in transitions:
            if next_state.id == target.id:
                properties["label"] = self.__format_number(prob)
                return properties
        return properties

    def _generate_node_js(self) -> None:
        self.nodes_js = ""
        for node in self.nt.nodes():
            node_attr = self.nt.nodes[node]
            label = node_attr.get("label", None)
            group = node_attr.get("group", None)
            color = node_attr.get("color", None)
            current = "{ id: " + str(node)
            if label is not None:
                current += f", label: `{label}`"
            if group is not None:
                current += f', group: "{group}"'
            if node in self.layout.layout["positions"]:
                current += f", x: {self.layout.layout['positions'][node]['x']}, y: {self.layout.layout['positions'][node]['y']}"
            if self.new_nodes_hidden and id != self.initial_node_id:
                current += ", hidden: true"
                current += ", physics: false"
            if color is not None:
                current += f', color: "{color}"'
            current += " },\n"
            self.nodes_js += current

    def _generate_edge_js(self) -> None:
        self.edges_js = ""
        for from_, to in self.nt.edges():
            edge_attr = self.nt.edges[(from_, to)]
            label = edge_attr.get("label", None)
            color = edge_attr.get("color", None)
            current = "{ from: " + str(from_) + ", to: " + str(to)
            if label is not None:
                current += f', label: "{label}"'
            if color is not None:
                current += f', color: "{color}"'
            if self.new_nodes_hidden:
                current += ", hidden: true"
                current += ", physics: false"
            current += " },\n"
            self.edges_js += current

    def generate_js(self) -> None:
        self._generate_node_js()
        self._generate_edge_js()
        self.options_js = json.dumps(self.layout.layout, indent=2)

    def set_options(self, options: str) -> None:
        """Set the options. Only use before calling show."""
        self.options_js = options

    def generate_html(self) -> str:
        return stormvogel.html_generation.generate_html(
            self.nodes_js,
            self.edges_js,
            self.options_js,
            self.name,
            self.width,
            self.height,
        )

    def generate_iframe(self) -> str:
        """Generate an iframe for the network, using the html."""
        return f"""
          <iframe
                id="{self.name}"
                width="{self.width + self.EXTRA_PIXELS}"
                height="{self.height + self.EXTRA_PIXELS}"
                sandbox="allow-scripts allow-same-origin"
                frameborder="0"
                srcdoc="{html.escape(self.generate_html())}"
                border:none !important;
                allowfullscreen webkitallowfullscreen mozallowfullscreen
          ></iframe>"""

    def generate_svg(self, width: int | None = None) -> str:
        """Generate an svg rendering for the network."""
        js = f"RETURN({self.network_wrapper}.getSvg());"
        res = self.server.result(js)[1:-1]
        unescaped = res.encode("utf-8").decode("unicode_escape")
        scaled = autoscale_svg(unescaped, width or self.width)
        return scaled

    def enable_exploration_mode(self, initial_node_id: int):
        """Every node becomes invisible. You can then click any node to reveal all of its successors. Call before adding any nodes to the network."""
        self.new_nodes_hidden = True
        self.initial_node_id = initial_node_id

    def get_positions(self) -> dict:
        """Get the current positions of the nodes on the canvas. Returns empty dict if unsucessful.
        Example result: {"0": {"x": 5, "y": 10}}"""
        if self.server is None:
            with self.debug_output:
                logging.warning(
                    "Server not initialized. Could not retrieve position data."
                )
            raise TimeoutError("Server not initialized.")
        try:
            positions: dict = json.loads(
                self.server.result(
                    f"""RETURN({self.network_wrapper}.network.getPositions())"""
                )
            )
            return positions
        except TimeoutError:
            with self.debug_output:
                logging.warning("Timed out. Could not retrieve position data.")
            raise TimeoutError("Timed out. Could not retrieve position data.")

    def show(self) -> None:
        with self.output:  ## If there was already a rendered network, clear it.
            ipd.clear_output()
        if len(self.model.get_states()) > self.max_states:
            with self.output:
                print(
                    f"This model has more than {self.max_states} states. If you want to proceed, set max_states to a higher value."
                    f"This is to prevent the browser from crashing, be careful."
                )
            return
        if len(self.model.get_states()) > self.max_physics_states:
            with self.output:
                print(
                    f"This model has more than {self.max_physics_states} states. If you want physics, set max_physics_states to a higher value."
                    f"Physics are disabled to prevent the browser from crashing, be careful."
                )
            self.layout.layout["physics"] = False
            self.layout.copy_settings()
        if self.layout.layout["misc"]["explore"]:
            self.nt.enable_exploration_mode(self.model.get_initial_state().id)
        underscored_labels = set(map(und, self.model.get_labels()))
        possible_groups = underscored_labels.union(
            {"states", "actions", "scheduled_actions"}
        )
        self.layout.set_possible_groups(possible_groups)
        self.options_js = json.dumps(self.layout.layout, indent=2)
        if self.use_iframe:
            iframe = self.generate_iframe()
        else:
            iframe = self.generate_html()
        with self.output:  # Display the iframe within the Output.
            ipd.clear_output()
            ipd.display(ipd.HTML(iframe))
        ipd.display(self.output)
        with self.debug_output:
            logging.info("Called Network.show")

    def __format_number(self, n: stormvogel.model.Value) -> str:
        """Call number_to_string in model.py while accounting for the settings specified in the layout object."""
        return stormvogel.model.number_to_string(
            n,
            self.layout.layout["numbers"]["fractions"],
            self.layout.layout["numbers"]["digits"],
            self.layout.layout["numbers"]["denominator_limit"],
        )

    def format_result(self, s: stormvogel.model.State) -> str:
        """Create a string that shows the result for this state. Starts with newline.
        If results are not enabled, then it returns the empty string."""
        if self.result is None or not self.layout.layout["results"]["show_results"]:
            return ""
        result_of_state = self.result.get_result_of_state(s)
        if result_of_state is None:
            return ""
        return (
            "\n"
            + self.layout.layout["results"]["result_symbol"]
            + " "
            + self.__format_number(result_of_state)
        )

    def __format_observations(self, s: stormvogel.model.State) -> str:
        """Create a String that shows the observation for this state (FOR POMDPs).
        Starts with newline."""
        if (
            s.observation is None
            or not self.layout.layout["state_properties"]["show_observations"]
        ):
            return ""
        else:
            return (
                "\n"
                + self.layout.layout["state_properties"]["observation_symbol"]
                + " "
                + str(s.observation.observation)
            )

    def __group_state(self, s: stormvogel.model.State, default: str) -> str:
        """Return the group of this state.
        That is, the label of s that has the highest priority, as specified by the user under edit_groups"""
        und_labels = set(map(lambda x: und(x), s.labels))
        res = list(
            filter(
                lambda x: x in und_labels, self.layout.layout["edit_groups"]["groups"]
            )
        )
        return und(res[0]) if res != [] else default

    def __group_action(
        self, s_id: int, a: stormvogel.model.Action, default: str
    ) -> str:
        """Return the group of this action. Only relevant for scheduling"""
        # Put the action in the group scheduled_actions if appropriate.
        if self.scheduler is None:
            return default

        choice = self.scheduler.get_choice_of_state(self.model.get_state_by_id(s_id))
        return "scheduled_actions" if a == choice else default

    def __format_rewards(
        self, s: stormvogel.model.State, a: stormvogel.model.Action
    ) -> str:
        """Create a string that contains either the state exit reward (if actions are not supported)
        or the reward of taking this action from this state. (if actions ARE supported)
        Starts with newline"""
        if not self.layout.layout["state_properties"]["show_rewards"]:
            return ""
        EMPTY_RES = "\n" + self.layout.layout["state_properties"]["reward_symbol"]
        res = EMPTY_RES
        for reward_model in self.model.rewards:
            if self.model.supports_actions():
                if a in s.available_actions():
                    reward = reward_model.get_state_action_reward(s, a)
                else:
                    reward = None
            else:
                reward = reward_model.get_state_reward(s)
            if reward is not None and not (
                not self.layout.layout["state_properties"]["show_zero_rewards"]
                and reward == 0
            ):
                res += f"\t{reward_model.name}: {self.__format_number(reward)}"
        if res == EMPTY_RES:
            return ""
        return res


class MplVisualization:
    DEFAULT_COLORS = {
        NodeType.STATE: "lightblue",
        NodeType.ACTION: "lightgreen",
        None: "grey",
    }
    DEFAULT_ALPHA = 0.25

    def __init__(
        self, model: stormvogel.model.Model, pos: dict[int, NDArray] | None = None
    ):
        self.G = ModelGraph.from_model(
            model, state_properties=lambda s: {"color": "blue"}
        )
        self._pos = pos or nx.random_layout(self.G)
        self._highlights: dict[int, str] = dict()
        self._edge_highlights: dict[tuple[int, int], str] = dict()
        self._fig = None

    @property
    def pos(self):
        return self._pos

    @pos.setter
    def pos(self, pos: dict[int, NDArray]):
        assert all([node in pos for node in self.G.nodes]), (
            "Not all nodes represented in pos"
        )
        self._pos = pos

    def highlight_state(self, state: stormvogel.model.State | int, color: str = "red"):
        """Highlight a state node of the visualization.
        Highlights will override the `node_color` parameter of the `update` method

        Parameters
        ----------
        state : stormvogel.model.State | int
            A stormvogel state or a valid state id
        color : str, default="red"
            The color used for highlighting
        """
        if isinstance(state, stormvogel.model.State):
            state = state.id
        node = state
        assert node in self.G.nodes, f"Node {node} not in graph"
        self._highlights[state] = color

    def highlight_action(
        self,
        state: stormvogel.model.State | int,
        action: stormvogel.model.Action,
        color: str = "red",
    ):
        """Highlight an action node of the visualization.
        Highlights will override the `node_color` parameter of the `update` method

        Parameters
        ----------
        state : stormvogel.model.State | int
            A stormvogel state or a valid state id
        action : stormvogel.model.Action
            A valid action for that state
        color : str, default="red"
            The color used for highlighting
        """
        if isinstance(state, stormvogel.model.State):
            state = state.id
        state_node = state
        assert state_node in self.G.nodes, f"Node {state_node} not in graph"
        action_node = self.G.state_action_id_map[state_node, action]
        self._highlights[action_node] = color

    def highlight_edge(self, from_: int, to_: int, color: str = "red"):
        """Highlight an edge (arrow) in the visualization.
        Highlights will override the `edge_color` parameter of the `update` method

        Parameters
        ----------
        from_ : Hashable
            The start node for the edge
        to_ : Hashable
            The target node for the edge
        color : str, default="red"
            The color to highlight the edge
        """
        self._edge_highlights[from_, to_] = color

    def clear_highlighting(self):
        """Clear all nodes that are marked for highlighting in the visualization"""
        self._highlights.clear()
        self._edge_highlights.clear()

    def add_to_ax(
        self,
        ax,
        node_alpha: dict[int, float] | float = 0.25,
        edge_alpha: dict[int, float] | float = 0.25,
        node_color: dict[int, str] | None = None,
        edge_color: dict[int, str] | None = None,
        node_size: int | dict[int, int] = 300,
    ):
        """Add the networkx graph to a matplotlib axes object

        Parameters
        ----------
        ax : matplotlib.axes.Axes
            A matplotlib axes object
        node_alpha : dict[Hashable, float] | float, default=0.25
            The alpha value for all nodes. If `node_alpha` is a dict, the keys must cover
            all node identifiers and provide a valid value for them
        edge_alpha : dict[Hashable, float] | float, default=0.25
            The alpha value for all edges. If `edge_alpha` is a dict, the keys must cover
            all edge identifiers and provide a valid value for them
        node_color : dict[Hashable, float] | None, default=None
            The color for all nodes. If `node_color` is a dict, the keys must cover
            all node identifiers and provide a valid value for them
        edge_color : dict[Hashable, float] | None, default=None
            The color for all edges. If `edge_color` is a dict, the keys must cover
            all edge identifiers and provide a valid value for them
        node_size : int | dict[Hashable, int], default=300
            The sizes for all nodes. If `node_size` is a dict, the keys must cover
            all node identifiers and provide a valid value for them

        Returns
        -------
        tuple[matplotlib.collections.PathCollection], list[matplotlib.patches.Patch]]
            A tuple consisting of the matplotlib path collection for the networkx nodes
            and a list of matplotlib patches for the edges
        """
        if isinstance(node_alpha, dict):
            assert all([n in node_alpha for n in self.G.nodes]), (
                "Not all nodes are present in node_alpha"
            )
        else:
            node_alpha = {n: node_alpha for n in self.G.nodes}
        if isinstance(edge_alpha, dict):
            assert all([e in edge_alpha for e in self.G.edges]), (
                "Not all edges are present in edge_alpha"
            )
        else:
            edge_alpha = {e: edge_alpha for e in self.G.edges}

        if node_color is None:
            node_color = {
                n: self.DEFAULT_COLORS.get(self.G.nodes[n].get("type_"), "grey")
                for n in self.G.nodes
            }
        else:
            assert all([n in node_color for n in self.G.nodes]), (
                "node_color is missing nodes"
            )

        if edge_color is None:
            edge_color = {e: "black" for e in self.G.edges}
        else:
            assert all([e in edge_color for e in self.G.edges]), (
                "edge_color is missing edges"
            )

        if isinstance(node_size, dict):
            assert all([n in node_size for n in self.G.nodes]), (
                "Not all nodes are present in node_size"
            )
        else:
            node_size = {n: node_size for n in self.G.nodes}

        # Now add highlights
        for node, color in self._highlights.items():
            node_color[node] = color
            node_alpha[node] = 1
        for edge, color in self._edge_highlights.items():
            edge_color[edge] = color
            edge_alpha[edge] = 1

        edges = nx.draw_networkx_edges(
            self.G,
            pos=self.pos,
            ax=ax,
            alpha=[edge_alpha[e] for e in self.G.edges],
            edge_color=[edge_color[e] for e in self.G.edges],
        )
        nodes = nx.draw_networkx_nodes(
            self.G,
            pos=self.pos,
            ax=ax,
            node_color=[node_color[n] for n in self.G.nodes],
            alpha=[node_alpha[n] for n in self.G.nodes],
            node_size=[node_size[n] for n in self.G.nodes],
        )
        return nodes, edges

    def update(
        self,
        node_alpha: dict[int, float] | float = 0.25,
        edge_alpha: dict[int, float] | float = 0.25,
        node_color: dict[int, str] | None = None,
        node_size: int | Sequence[int] = 300,
        interactive: bool = False,
        title: str | None = None,
        figsize: tuple[float, float] | None = None,
    ):
        """Update the internal matplotlib figure with the new parameters
        This does not trigger a draw call but will return the figure which might
        draw it depending on your environment.

        Parameters
        ----------
        node_alpha : dict[Hashable, float] | float, default=0.25
            The alpha value for all nodes. If `node_alpha` is a dict, the keys must cover
            all node identifiers and provide a valid value for them
        edge_alpha : dict[Hashable, float] | float, default=0.25
            The alpha value for all edges. If `edge_alpha` is a dict, the keys must cover
            all edge identifiers and provide a valid value for them
        node_color : dict[Hashable, float] | None, default=None
            The color for all nodes. If `node_color` is a dict, the keys must cover
            all node identifiers and provide a valid value for them
        edge_color : dict[Hashable, float] | None, default=None
            The color for all edges. If `edge_color` is a dict, the keys must cover
            all edge identifiers and provide a valid value for them
        title : str | None, default=None
            The title of the matplotlib axes object
        figsize: tuple[float, float] | None, default=None
            The figsize in inches, same as the keyword argument for matplotlib.figure.Figure

        Returns
        -------
        The updated figure instance
        """
        if self._fig is None:
            self._fig, ax = plt.subplots(figsize=figsize)
        else:
            if figsize is not None:
                w, h = figsize
                # INFO: This triggers an update everytime
                self._fig.set_figwidth(w)
                self._fig.set_figheight(h)
            ax = self._fig.gca()
            ax.clear()
        fig = self._fig
        nodes, _ = self.add_to_ax(
            ax, node_alpha, edge_alpha, node_color, node_size=node_size
        )
        if title is not None:
            ax.set_title(title)
        node_list = list(self.G.nodes)

        def update_title(ind):
            idx = ind["ind"][0]
            node = node_list[idx]
            ax.set_title(f"{self.G.nodes[node]['type_'].name}: {node}")

        def hover(event):
            cont, ind = nodes.contains(event)
            if cont:
                update_title(ind)
                fig.canvas.draw_idle()

        if interactive:
            fig.canvas.mpl_connect("motion_notify_event", hover)
        return fig

    def show(
        self,
        node_alpha: dict[int, float] | float = 0.25,
        edge_alpha: dict[int, float] | float = 0.25,
        node_color: dict[int, str] | None = None,
        node_size: int | Sequence[int] = 300,
        interactive: bool = False,
        title: str | None = None,
    ):
        fig = self.update(
            node_alpha, edge_alpha, node_color, node_size, interactive, title
        )
        plt.show()
        return fig


class Visualization(stormvogel.displayable.Displayable):
    """Handles visualization of a Model using a Network from stormvogel.network."""

    ACTION_ID_OFFSET: int = 10**10
    # In the visualization, both actions and states are nodes with an id.
    # This offset is used to keep their ids from colliding. It should be some high constant.

    def __init__(
        self,
        model: stormvogel.model.Model,
        result: stormvogel.result.Result | None = None,
        scheduler: stormvogel.result.Scheduler | None = None,
        layout: stormvogel.layout.Layout = stormvogel.layout.DEFAULT(),
        output: widgets.Output | None = None,
        debug_output: widgets.Output = widgets.Output(),
        use_iframe: bool = False,
        do_init_server: bool = True,
        do_display: bool = True,
        max_states: int = 1000,
        max_physics_states: int = 500,
    ) -> None:
        """Create and show a visualization of a Model using a visjs Network
        Args:
            model (Model): The stormvogel model to be displayed.
            result (Result, optional): A result associatied with the model.
                The results are displayed as numbers on a state. Enable the layout editor for options.
                If this result has a scheduler, then the scheduled actions will have a different color etc. based on the layout
            scheduler (Scheduler, optional): The scheduled actions will have a different color etc. based on the layout
                If both result and scheduler are set, then scheduler takes precedence.
            layout (Layout): Layout used for the visualization.
            show_editor (bool): Show an interactive layout editor.
            use_iframe (bool): Wrap the generated html inside of an IFrame.
                In some environments, the visualization works better with this enabled.
            output (widgets.Output): The output widget in which the network is rendered.
                Whether this widget is also displayed automatically depends on do_display.
            debug_output (widgets.Output): Output widget that can be used to debug interactive features.
            do_init_server (bool): Initialize a local server that is used for communication between Javascript and Python.
                If this is set to False, then exporting network node positions and svg/pdf/latex is impossible.
            do_display (bool): The Visualization displays on its own iff this is enabled.
                This option is useful for situations where you want to manage the displaying externally.
            max_states (int): If the model has more states, then the network is not displayed.
            max_physics_states (int): If the model has more states, then physics are disabled.
        Returns: Visualization object.
        """
        super().__init__(output, do_display, debug_output)
        self.name: str = random_word(10)
        self.model: stormvogel.model.Model = model
        self.result: stormvogel.result.Result | None = result
        self.scheduler: stormvogel.result.Scheduler | None = scheduler
        self.use_iframe: bool = use_iframe
        self.max_states: int = max_states
        self.max_physics_states: int = max_physics_states
        # If a scheduler was not set explictly, but a result was set, then take the scheduler from the results.
        self.layout: stormvogel.layout.Layout = layout
        if self.scheduler is None:
            if self.result is not None:
                self.scheduler = self.result.scheduler

        # Set "scheduler" as an active group iff it is present.
        if self.scheduler is not None:
            layout.add_active_group("scheduled_actions")
        else:  # Otherwise, disable it
            layout.remove_active_group("scheduled_actions")

        self.do_init_server: bool = do_init_server
        self.__create_nt()
        self.network_action_map_id: dict[tuple[int, stormvogel.model.Action], int] = {}
        # Relate state ids and actions to the node id of the action for this state in the network.

    def __create_nt(self) -> None:
        """Reload the node positions and create the network."""
        self.nt: stormvogel.network.Network = stormvogel.network.Network(
            name=self.name,
            width=self.layout.layout["misc"]["width"],
            height=self.layout.layout["misc"]["height"],
            output=self.output,
            debug_output=self.debug_output,
            do_display=False,
            do_init_server=self.do_init_server,
            positions=self.layout.layout["positions"],
            use_iframe=self.use_iframe,
        )

    def show(self) -> None:
        """(Re-)load the Network and display if self.do_display is True.
        Important side effect: all changes to the layout are applied.
        This also includes updating the edit groups.
        """
        with self.output:  ## If there was already a rendered network, clear it.
            ipd.clear_output()
        if len(self.model.get_states()) > self.max_states:
            with self.output:
                print(
                    f"This model has more than {self.max_states} states. If you want to proceed, set max_states to a higher value."
                    f"This is to prevent the browser from crashing, be careful."
                )
            return
        if len(self.model.get_states()) > self.max_physics_states:
            with self.output:
                print(
                    f"This model has more than {self.max_physics_states} states. If you want physics, set max_physics_states to a higher value."
                    f"Physics are disabled to prevent the browser from crashing, be careful."
                )
            self.layout.layout["physics"] = False
            self.layout.copy_settings()
        self.__create_nt()
        if self.layout.layout["misc"]["explore"]:
            self.nt.enable_exploration_mode(self.model.get_initial_state().id)

        # Set the (possibly updated) possible edit groups
        underscored_labels = set(map(und, self.model.get_labels()))
        possible_groups = underscored_labels.union(
            {"states", "actions", "scheduled_actions"}
        )
        self.layout.set_possible_groups(possible_groups)

        self.__add_states()
        self.__add_transitions()
        self.nt.set_options(str(self.layout))
        if self.nt is not None:
            self.nt.show()
        self.maybe_display_output()

    def clear(self) -> None:
        """Clear visualization."""
        with self.output:
            ipd.clear_output()

    def update(self) -> None:
        """Tries to update an existing visualization to apply layout changes WITHOUT reloading. If show was not called before, nothing happens."""
        if self.nt is not None:
            self.nt.update_options(str(self.layout))

    def __group_state(self, s: stormvogel.model.State, default: str) -> str:
        """Return the group of this state.
        That is, the label of s that has the highest priority, as specified by the user under edit_groups"""
        und_labels = set(map(lambda x: und(x), s.labels))
        res = list(
            filter(
                lambda x: x in und_labels, self.layout.layout["edit_groups"]["groups"]
            )
        )
        return und(res[0]) if res != [] else default

    def __group_action(
        self, s_id: int, a: stormvogel.model.Action, default: str
    ) -> str:
        """Return the group of this action. Only relevant for scheduling"""
        # Put the action in the group scheduled_actions if appropriate.
        if self.scheduler is None:
            return default

        choice = self.scheduler.get_choice_of_state(self.model.get_state_by_id(s_id))
        return "scheduled_actions" if a == choice else default

    def __add_states(self) -> None:
        """For each state in the model, add a node to the graph. I"""
        if self.nt is None:
            return
        for state in self.model.get_states().values():
            res = self.__format_result(state)
            observations = self.__format_observations(state)
            rewards = self.__format_rewards(state, stormvogel.model.EmptyAction)
            group = self.__group_state(state, "states")
            id_label_part = (
                f"{state.id}\n"
                if self.layout.layout["state_properties"]["show_ids"]
                else ""
            )

            color = None

            result_colors = self.layout.layout["results"]["result_colors"]
            if result_colors and self.result is not None:
                result = self.result.get_result_of_state(state)
                max_result = self.result.maximum_result()
                if isinstance(result, (int, float, Fraction)) and isinstance(
                    max_result, (int, float, Fraction)
                ):
                    color1 = self.layout.layout["results"]["max_result_color"]
                    color2 = self.layout.layout["results"]["min_result_color"]
                    factor = result / max_result if max_result != 0 else 0
                    color = blend_colors(color1, color2, float(factor))

            self.nt.add_node(
                state.id,
                label=id_label_part
                + ",".join(state.labels)
                + rewards
                + res
                + observations,
                group=group,
                color=color,
            )

    def __add_transitions(self) -> None:
        """For each transition in the model, add a transition in the graph.
        Also handles creating nodes for actions and their respective transitions.
        Note that an action may appear multiple times in the model with a different state as source."""
        if self.nt is None:
            return
        network_action_id = self.ACTION_ID_OFFSET
        # In the visualization, both actions and states are nodes, so we need to keep track of how many actions we already have.
        for state_id, transition in self.model.transitions.items():
            for action, branch in transition.transition.items():
                if action == stormvogel.model.EmptyAction:
                    # Only draw probabilities
                    for prob, target in branch.branch:
                        self.nt.add_edge(
                            state_id,
                            target.id,
                            label=self.__format_number(prob),
                        )
                else:
                    group = self.__group_action(state_id, action, "actions")
                    reward = self.__format_rewards(
                        self.model.get_state_by_id(state_id), action
                    )

                    # Add the action's node
                    self.nt.add_node(
                        id=network_action_id,
                        label=",".join(action.labels) + reward,
                        group=group,
                    )
                    if group == "scheduled_actions":
                        try:
                            edge_color = self.layout.layout["groups"][
                                "scheduled_actions"
                            ]["color"]["border"]
                        except KeyError:
                            edge_color = None
                    else:
                        edge_color = None

                    # Add transition from this state TO the action.
                    self.nt.add_edge(state_id, network_action_id, color=edge_color)  # type: ignore
                    # Add transition FROM the action to the states in its branch.
                    for prob, target in branch.branch:
                        self.network_action_map_id[state_id, action] = network_action_id
                        self.nt.add_edge(
                            network_action_id,
                            target.id,
                            label=self.__format_number(prob),
                            color=edge_color,
                        )
                    network_action_id += 1

    def __format_number(self, n: stormvogel.model.Value) -> str:
        """Call number_to_string in model.py while accounting for the settings specified in the layout object."""
        return stormvogel.model.number_to_string(
            n,
            self.layout.layout["numbers"]["fractions"],
            self.layout.layout["numbers"]["digits"],
            self.layout.layout["numbers"]["denominator_limit"],
        )

    def __format_rewards(
        self, s: stormvogel.model.State, a: stormvogel.model.Action
    ) -> str:
        """Create a string that contains either the state exit reward (if actions are not supported)
        or the reward of taking this action from this state. (if actions ARE supported)
        Starts with newline"""
        if not self.layout.layout["state_properties"]["show_rewards"]:
            return ""
        EMPTY_RES = "\n" + self.layout.layout["state_properties"]["reward_symbol"]
        res = EMPTY_RES
        for reward_model in self.model.rewards:
            if self.model.supports_actions():
                if a in s.available_actions():
                    reward = reward_model.get_state_action_reward(s, a)
                else:
                    reward = None
            else:
                reward = reward_model.get_state_reward(s)
            if reward is not None and not (
                not self.layout.layout["state_properties"]["show_zero_rewards"]
                and reward == 0
            ):
                res += f"\t{reward_model.name}: {self.__format_number(reward)}"
        if res == EMPTY_RES:
            return ""
        return res

    def __format_result(self, s: stormvogel.model.State) -> str:
        """Create a string that shows the result for this state. Starts with newline.
        If results are not enabled, then it returns the empty string."""
        if self.result is None or not self.layout.layout["results"]["show_results"]:
            return ""
        result_of_state = self.result.get_result_of_state(s)
        if result_of_state is None:
            return ""
        return (
            "\n"
            + self.layout.layout["results"]["result_symbol"]
            + " "
            + self.__format_number(result_of_state)
        )

    def __format_observations(self, s: stormvogel.model.State) -> str:
        """Create a String that shows the observation for this state (FOR POMDPs).
        Starts with newline."""
        if (
            s.observation is None
            or not self.layout.layout["state_properties"]["show_observations"]
        ):
            return ""
        else:
            return (
                "\n"
                + self.layout.layout["state_properties"]["observation_symbol"]
                + " "
                + str(s.observation.observation)
            )

    def generate_html(self) -> str:
        """Get HTML code that can be used to show this visualization."""
        return self.nt.generate_html()

    def generate_iframe(self) -> str:
        """Get the HTML code that can be used to show this visualization, wrapped in an IFrame."""
        return self.nt.generate_iframe()

    def generate_svg(self) -> str:
        """Generate an svg image of the network."""
        return self.nt.generate_svg()

    def export(self, output_format: str, filename: str = "export") -> None:
        """
        Export the visualization to your preferred output format.
        The appropriate file extension will be added automatically.

        Parameters:
            output_format (str): Desired export format.
            filename (str): Base name for the exported file.

        Supported output formats (not case-sensitive):

            "HTML"    → An interactive .html file (e.g., draggable nodes)
            "IFrame"  → Exports as an <iframe> wrapped HTML in a .html file
            "PDF"     → Exports to .pdf (via conversion from SVG)
            "SVG"     → Exports to .svg vector image
        """
        output_format = output_format.lower()
        filename_base = pathlib.Path(filename).with_suffix(
            ""
        )  # remove extension if present

        if output_format == "html":
            html = self.generate_html()
            (filename_base.with_suffix(".html")).write_text(html, encoding="utf-8")

        elif output_format == "iframe":
            iframe = self.generate_iframe()
            (filename_base.with_suffix(".html")).write_text(iframe, encoding="utf-8")

        elif output_format == "svg":
            svg = self.generate_svg()
            (filename_base.with_suffix(".svg")).write_text(svg, encoding="utf-8")

        elif output_format == "pdf":
            svg = self.generate_svg()
            cairosvg.svg2pdf(
                bytestring=svg.encode("utf-8"), write_to=filename_base.name + ".pdf"
            )

        elif output_format == "latex":
            svg = self.generate_svg()
            # Create the 'export' folder if it doesn't exist
            export_folder = pathlib.Path(filename_base)
            export_folder.mkdir(parents=True, exist_ok=True)
            pdf_filename = filename_base.with_suffix(".pdf")
            # Convert SVG to PDF
            cairosvg.svg2pdf(
                bytestring=svg.encode("utf-8"),
                write_to=str(export_folder / pdf_filename),
            )

            # Create the LaTeX file
            latex_content = f"""\\documentclass{{article}}
\\usepackage{{graphicx}}
\\begin{{document}}
\\begin{{figure}}[h!]
\\centering
\\includegraphics[width=\\textwidth]{{{pdf_filename.name}}}
\\caption{{Generated using Stormvogel. TODO insert citing instructions}}
\\end{{figure}}
\\end{{document}}
"""
            # Write the LaTeX code to a .tex file
            (export_folder / filename_base.with_suffix(".tex")).write_text(
                latex_content, encoding="utf-8"
            )

        else:
            raise RuntimeError(f"Export format not supported: {output_format}")

    def get_positions(self) -> dict:
        """Get Network's current (interactive, dragged) node positions. Only works if show was called before.
        NOTE: This method only works after the network is properly loaded."""
        return self.nt.get_positions() if self.nt is not None else {}

    def __to_state_action_sequence(
        self, path: simulator.Path
    ) -> list[stormvogel.model.Action | stormvogel.model.State]:
        """Convert a Path to a list containing actions and states."""
        res: list[stormvogel.model.Action | stormvogel.model.State] = [
            self.model.get_initial_state()
        ]
        for _, v in path.path.items():
            if isinstance(v, tuple):
                res += list(v)
            else:
                res.append(v)
        return res

    def highlight_state(self, s_id: int, color: str | None = "red"):
        """Highlight a state in the model by changing its color. You can clear the current color by setting it to None."""
        self.nt.set_node_color(s_id, color)

    def highlilght_action(
        self, s_id: int, action: stormvogel.model.Action, color: str | None = "red"
    ):
        """Highlight an action in the model by changing its color. You can clear the current color by setting it to None."""
        try:
            nt_id = self.network_action_map_id[s_id, action]
            self.nt.set_node_color(nt_id, color)
        except KeyError:
            warnings.warn(
                "Tried to highlight an action that is not present in this model."
            )

    def highlight_state_set(self, state_ids: set[int], color: str | None = "blue"):
        """Highlight a set of states in the model by changing their color. You can clear the current color by setting it to None."""
        for s_id in state_ids:
            self.nt.set_node_color(s_id, color)

    def highlight_action_set(
        self,
        state_action_set: set[tuple[int, stormvogel.model.Action]],
        color: str = "red",
    ):
        """Highlight a set of actions in the model by changing their color. You can clear the current color by setting it to None."""
        for s_id, a in state_action_set:
            self.highlilght_action(s_id, a, color)

    def highlight_decomposition(
        self,
        decomp: list[tuple[set[int], set[tuple[int, stormvogel.model.Action]]]],
        colors: list[str] | None = None,
    ):
        """Highlight a set of tuples of (states and actions) in the model by changing their color.
        Args:
            decomp: A list of tuples (states, actions)
            colors (optional): A list of colors for the decompossitions. Random colors are picked by default."""
        for n, v in enumerate(decomp):
            if colors is None:
                color = random_color()
            else:
                color = colors[n]
            self.highlight_state_set(v[0], color)
            self.highlight_action_set(v[1], color)

    def clear_highlighting(self):
        """Clear all highlighting that is currently active, returing all states to their original colors."""
        for s_id in self.model.get_states():
            self.nt.set_node_color(s_id, None)
        for a_id in self.network_action_map_id.values():
            self.nt.set_node_color(a_id, None)

    def highlight_path(
        self,
        path: simulator.Path,
        color: str,
        delay: float = 1,
        clear: bool = True,
    ) -> None:
        """Highlight the path that is provided as an argument in the model.
        Args:
            path (simulator.Path): The path to highlight.
            color (str | None): The color that the highlighted states should get (in HTML color standard).
                Set to None, in order to clear existing highlights on this path.
            delay (float): If not None, there will be a pause of a specified time before highlighting the next state in the path.
            clear (bool): Clear the highlighting of a state after it was highlighted. Only works if delay is not None.
                This is particularly useful for highlighting paths with loops."""
        seq = self.__to_state_action_sequence(path)
        for i, v in enumerate(seq):
            if isinstance(v, stormvogel.model.State):
                self.nt.set_node_color(v.id, color)
                sleep(delay)
                if clear:
                    self.nt.set_node_color(v.id, None)
            elif (
                isinstance(v, stormvogel.model.Action)
                and (seq[i - 1].id, v) in self.network_action_map_id
            ):
                node_id = self.network_action_map_id[seq[i - 1].id, v]
                self.nt.set_node_color(node_id, color)
                sleep(delay)
                if clear:
                    self.nt.set_node_color(node_id, None)
