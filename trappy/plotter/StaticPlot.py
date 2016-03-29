#    Copyright 2016-2016 ARM Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Base matplotlib plotter module"""
from abc import abstractmethod, ABCMeta
from collections import defaultdict as ddict
import matplotlib.pyplot as plt
from trappy.plotter import AttrConf
from trappy.plotter.Constraint import ConstraintManager
from trappy.plotter.PlotLayout import PlotLayout
from trappy.plotter.AbstractDataPlotter import AbstractDataPlotter
from trappy.plotter.ColorMap import ColorMap



class StaticPlot(AbstractDataPlotter):
    """
    This class uses :mod:`trappy.plotter.Constraint.Constraint` to
    represent different permutations of input parameters. These
    constraints are generated by creating an instance of
    :mod:`trappy.plotter.Constraint.ConstraintManager`.

    :param traces: The input data
    :type traces: a list of :mod:`trappy.trace.FTrace`,
        :mod:`trappy.trace.SysTrace`, :mod:`trappy.trace.BareTrace`
        or :mod:`pandas.DataFrame` or a single instance of them.

    :param column: specifies the name of the column to
           be plotted.
    :type column: (str, list(str))

    :param templates: TRAPpy events

        .. note::

                This is not required if a :mod:`pandas.DataFrame` is
                used

    :type templates: :mod:`trappy.base.Base`

    :param filters: Filter the column to be plotted as per the
        specified criteria. For Example:
        ::

            filters =
                    {
                        "pid": [ 3338 ],
                        "cpu": [0, 2, 4],
                    }
    :type filters: dict

    :param per_line: Used to control the number of graphs
        in each graph subplot row
    :type per_line: int

    :param concat: Draw all the pivots on a single graph
    :type concat: bool

    :param permute: Draw one plot for each of the traces specified
    :type permute: bool

    :param drawstyle: This argument is forwarded to the matplotlib
        corresponding :func:`matplotlib.pyplot.plot` call

        drawing style.

        .. note::

            step plots are not currently supported for filled
            graphs

    :param xlim: A tuple representing the upper and lower xlimits
    :type xlim: tuple

    :param ylim: A tuple representing the upper and lower ylimits
    :type ylim: tuple

    :param title: A title describing all the generated plots
    :type title: str

    :param style: Created pre-styled graphs loaded from
        :mod:`trappy.plotter.AttrConf.MPL_STYLE`
    :type style: bool

    :param signals: A string of the type event_name:column
        to indicate the value that needs to be plotted

        .. note::

            - Only one of `signals` or both `templates` and
              `columns` should be specified
            - Signals format won't work for :mod:`pandas.DataFrame`
              input

    :type signals: str

    :param legend_ncol: A positive integer that represents the
        number of columns in the legend
    :type legend_ncol: int
    """
    __metaclass__ = ABCMeta

    def __init__(self, traces, templates, **kwargs):
        self._fig = None
        self._layout = None
        super(StaticPlot, self).__init__(traces=traces,
                                         templates=templates)

        self.set_defaults()

        for key in kwargs:
            if key in AttrConf.ARGS_TO_FORWARD:
                self._attr["args_to_forward"][key] = kwargs[key]
            else:
                self._attr[key] = kwargs[key]

        if "signals" in self._attr:
            self._describe_signals()

        self._check_data()

        if "column" not in self._attr:
            raise RuntimeError("Value Column not specified")

        zip_constraints = not self._attr["permute"]
        self.c_mgr = ConstraintManager(traces, self._attr["column"],
                                       self.templates, self._attr["pivot"],
                                       self._attr["filters"], zip_constraints)

    def savefig(self, *args, **kwargs):
        """Save the plot as a PNG fill. This calls into
        :mod:`matplotlib.figure.savefig`
        """

        if self._fig is None:
            self.view()
        self._fig.savefig(*args, **kwargs)

    @abstractmethod
    def set_defaults(self):
        """Sets the default attrs"""
        self._attr["width"] = AttrConf.WIDTH
        self._attr["length"] = AttrConf.LENGTH
        self._attr["per_line"] = AttrConf.PER_LINE
        self._attr["concat"] = AttrConf.CONCAT
        self._attr["filters"] = {}
        self._attr["style"] = True
        self._attr["permute"] = False
        self._attr["pivot"] = AttrConf.PIVOT
        self._attr["xlim"] = AttrConf.XLIM
        self._attr["ylim"] = AttrConf.XLIM
        self._attr["title"] = AttrConf.TITLE
        self._attr["args_to_forward"] = {}
        self._attr["map_label"] = {}
        self._attr["_legend_handles"] = []
        self._attr["_legend_labels"] = []
        self._attr["legend_ncol"] = AttrConf.LEGEND_NCOL

    def view(self, test=False):
        """Displays the graph"""

        if test:
            self._attr["style"] = True
            AttrConf.MPL_STYLE["interactive"] = False

        permute = self._attr["permute"] and not self._attr["concat"]
        if self._attr["style"]:
            with plt.rc_context(AttrConf.MPL_STYLE):
                self._resolve(permute, self._attr["concat"])
        else:
            self._resolve(permute, self._attr["concat"])

    def make_title(self, constraint, pivot, permute, concat):
        """Generates a title string for an axis"""
        if concat:
            return str(constraint)

        if permute:
            return constraint.get_data_name()
        elif pivot != AttrConf.PIVOT_VAL:
            return "{0}: {1}".format(self._attr["pivot"], self._attr["map_label"].get(pivot, pivot))
        else:
            return ""

    def add_to_legend(self, series_index, handle, constraint, pivot, concat, permute):
        """
        Add series handles and names to the legend
        A handle is returned from a plot on an axis
        e.g. Line2D from axis.plot()
        """
        self._attr["_legend_handles"][series_index] = handle
        legend_labels = self._attr["_legend_labels"]

        if concat and pivot == AttrConf.PIVOT_VAL:
            legend_labels[series_index] = self._attr["column"]
        elif concat:
            legend_labels[series_index] = "{0}: {1}".format(
                self._attr["pivot"],
                self._attr["map_label"].get(pivot, pivot)
            )
        elif permute:
            legend_labels[series_index] = constraint._template.name + ":" + constraint.column
        else:
            legend_labels[series_index] = str(constraint)

    def _resolve(self, permute, concat):
        """Determine what data to plot on which axis"""
        pivot_vals, len_pivots = self.c_mgr.generate_pivots(permute)
        pivot_vals = list(pivot_vals)

        num_of_axes = len(self.c_mgr) if concat else len_pivots

        # Create a 2D Layout
        self._layout = PlotLayout(
            self._attr["per_line"],
            num_of_axes,
            width=self._attr["width"],
            length=self._attr["length"],
            title=self._attr['title'])

        self._fig = self._layout.get_fig()

        # Determine what constraint to plot and the corresponding pivot value
        if permute:
            legend_len = self.c_mgr._max_len
            pivots = [y for _, y in pivot_vals]
            c_dict = {c : str(c) for c in self.c_mgr}
            c_list = sorted(c_dict.items(), key=lambda x: (x[1].split(":")[-1], x[1].split(":")[0]))
            constraints = [c[0] for c in c_list]
            cp_pairs = [(c, p) for c in constraints for p in sorted(set(pivots))]
        else:
            legend_len = len_pivots if concat else len(self.c_mgr)
            pivots = pivot_vals
            cp_pairs = [(c, p) for c in self.c_mgr for p in pivots if p in c.result]

        # Initialise legend data and colormap
        self._attr["_legend_handles"] = [None] * legend_len
        self._attr["_legend_labels"] = [None] * legend_len
        self._cmap = ColorMap(legend_len)

        # Group constraints/series with the axis they are to be plotted on
        figure_data = ddict(list)
        for i, (constraint, pivot) in enumerate(cp_pairs):
            axis = self._layout.get_axis(constraint.trace_index if concat else i)
            figure_data[axis].append((constraint, pivot))

        # Plot each axis
        for axis, series_list in figure_data.iteritems():
            self.plot_axis(
                axis,
                series_list,
                permute,
                self._attr["concat"],
                self._attr["args_to_forward"]
            )

        # Show legend
        legend = self._fig.legend(self._attr["_legend_handles"],
                         self._attr["_legend_labels"],
                         loc='lower center',
                         ncol=self._attr["legend_ncol"],
                         borderaxespad=0.)
        legend.get_frame().set_facecolor('#F4F4F4')

        self._layout.finish(num_of_axes)

    def plot_axis(self, axis, series_list, permute, concat, args_to_forward):
        """Internal Method called to plot data (series_list) on a given axis"""
        raise NotImplementedError("Method Not Implemented")
