import h5py
import numpy as np
import seaborn as sns
from collections import defaultdict
from scipy.stats import linregress, iqr
import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerLineCollection
from matplotlib.collections import LineCollection
from matplotlib.patches import Rectangle
import warnings
import time
from sklearn.ensemble import ExtraTreesRegressor
from matplotlib.backends.backend_pdf import PdfPages
import io


def sensitivity_analysis(
        population=None, X=None, y=None, x0_idx=None, x0_str=None, input_str=None, output_str=None, no_lsa=False,
        indep_norm=None, dep_norm=None, n_neighbors=60, max_neighbors=np.inf, beta=2., rel_start=.5, p_baseline=.05,
        confound_baseline=.5, r_ceiling_val=None, important_dict=None, global_log_indep=None, global_log_dep=None,
        perturb_range=.1, verbose=True, repeat=False, save=True, save_format='png', save_txt=True, perturb_all=False,
        uniform=False, jupyter=False):
    """
    the main function to run sensitivity analysis. provide either
        1) a PopulationStorage object (_population_)
        2) the independent and dependent variables as two separate arrays (_X_ and _y_)

    :param population: PopulationStorage object.
    :param X: 2d np array or None (default). columns = variables, rows = examples
    :param y: 2d np array or None (default). columns = variables, rows = examples
    :param x0_idx: int or None (default). index of the center in the X array/PopulationStorage object
    :param x0_str: string or None. specify either x0_idx or x0_string, but not both. if both are None, a random
        center is selected. x0_string represents the center point of the neighbor search. accepted strings are 'best' or
        any of the objective names
    :param input_str: string representing the independent variable. accepted strings are 'parameter', 'p,' objective,'
        'o,' 'feature,' 'f.'
    :param output_str: string representing the independent variable. accepted strings are 'objective,'
        'o,' 'feature,' 'f.'
    :param no_lsa: bool; if true, sensitivity analysis is not done, but the LSA object is returned. this allows for
        convenient unfiltered plotting of the optimization.
    :param indep_norm: string specifying how the dependent variable is normalized. 'loglin,' 'lin', or 'none' are
        accepted strings.
    :param dep_norm: string specifying how the independent variable is normalized. 'loglin,' 'lin', or 'none' are
        accepted strings.
    :param n_neighbors: int. The minimum amount of neighbors desired to be selected during the first pass.
    :param max_neighbors: int or None. The maximum amount of neighbors desired to be selected during the first pass.
        If None, no maximum.
    :param beta: float. represents the maximum distance a nonquery parameter can vary relative to the query parameter
        during the first pass, i.e., a scalar factor.
    :param rel_start: float. represents the maximum distance a nonquery confound parameter can vary relative to the query
        parameter during clean-up. if repeat is True, the relative allowed distance is gradually decremented until there
        are no more confounds.
    :param p_baseline: float between 0 and 1. Threshold for statistical significance.
    :param confound_baseline: float between 0 and 1. Threshold for the absolute R coefficient a variable needs in
        order to be considered a confound.
    :param r_ceiling_val: float between 0 and 1, or None. If specified, all the colormaps in first_pass_colormaps.pdf
        will have a maximum of r_ceiling_val. This is to standardize the plots.
    :param important_dict: Dictionary. The keys are strings (dependent variable names) and the values are lists of strings
        (independent variable names). The user can specify what s/he already knows are important relationships.
    :param global_log_indep: string or None. if indep_norm is 'loglin,' user can specify if normalization should be
        global or local. accepted strings are 'local' or 'global.'
    :param global_log_dep: string or None. if dep_norm is 'loglin,' user can specify if normalization should be
        global or local. accepted strings are 'local' or 'global.'
    :param perturb_range: float. The range around x0 to sample points from for the perturbation matrix. if the parameter
        values are linearly scaled (indep_norm is 'lin') and the range is .1, that means the range is 5% in either
        direction of x0.
    :param verbose: Bool; if true, prints out which variables were confounds in the set of points. Once can also
        see the confounds in first_pass_colormaps.pdf if save is True.
    :param repeat: Bool; if true, repeatedly checks the set of points to see if there are still confounds.
    :param save: Bool; if true, all neighbor search plots are saved.
    :param save_format: string: 'png,' 'pdf,' or 'svg.' 'png' is the default. this specifies how the scatter plots
        will be saved (if they are saved)
    :param save_txt: bool; if True, will save the printed output in a text file
    :param perturb_all: bool; if True, will generate a set of parameter values for all parameters instead of the
        parameters for which not enough neighbors were found during sensitivity analysis
    :param uniform: bool; if True, will select a set of n_neighbor points after the clean up process that are as uniformly
        spaced as possible (wrt the query parameter)
    :param jupyter: bool. set as True if running in jupyter notebook
    :return: PopulationStorage and LSA object. The PopulationStorage contains the perturbations. The LSA object is
        for plotting and saving results of the optimization and/or sensitivity analysis.
    """

    #static
    feat_strings = ['f', 'feature', 'features']
    obj_strings = ['o', 'objective', 'objectives']
    param_strings = ['parameter', 'p', 'parameters']
    lsa_heatmap_values = {'confound': .35, 'no_neighbors': .1}

    if jupyter and save:
        raise RuntimeError("Automatically saving the figures while running sensitivity analysis in a Jupyter Notebook "
                           "is not supported.")
    check_save_format_correct(save_format)
    check_data_format_correct(population, X, y)

    #prompt user
    if x0_str is None and population is not None: x0_str = prompt_indiv(list(population.objective_names))
    if input_str is None and population is not None: input_str = prompt_input()
    if output_str is None and population is not None:  output_str = prompt_output()
    if indep_norm is None: indep_norm = prompt_norm("independent")
    if dep_norm is None: dep_norm = prompt_norm("dependent")

    if indep_norm == 'loglin' and global_log_indep is None: global_log_indep = prompt_global_vs_local("n independent")
    if dep_norm == 'loglin' and global_log_dep is None: global_log_dep = prompt_global_vs_local(" dependent")

    #set variables based on user input
    if population is None:
        input_names = np.array(["input " + str(i) for i in range(X.shape[1])])
        y_names = np.array(["output " + str(i) for i in range(y.shape[1])])
    else:
        input_names, y_names = get_variable_names(population, input_str, output_str, obj_strings, feat_strings,
                                                  param_strings)

    if save_txt:
        txt_file = io.open("data/lsa/{}{}{}{}{}{}_output_txt.txt".format(*time.localtime()), "w", encoding='utf-8')
        write_settings_to_file(
            input_str, output_str, x0_str, indep_norm, dep_norm, global_log_indep, global_log_dep, beta, rel_start,
            confound_baseline, p_baseline, repeat, txt_file)
    else:
        txt_file = None
    if important_dict is not None: check_user_importance_dict_correct(important_dict, input_names, y_names)
    inp_out_same = (input_str in feat_strings and output_str in feat_strings) or \
                   (input_str in obj_strings and output_str in obj_strings)

    #process and potentially normalize data
    if population is not None: X, y = pop_to_matrix(population, input_str, output_str, param_strings, obj_strings)
    if x0_idx is None:
        if population is not None:
            x0_idx = x0_to_index(population, x0_str, X, input_str, param_strings, obj_strings)
        else:
            x0_idx = np.random.randint(0, X.shape[1])

    X_processed_data, X_crossing_loc, X_zero_loc, X_pure_neg_loc = process_data(X)
    y_processed_data, y_crossing_loc, y_zero_loc, y_pure_neg_loc = process_data(y)
    X_normed, scaling, logdiff_array, logmin_array, diff_array, min_array = normalize_data(
        X_processed_data, X_crossing_loc, X_zero_loc, X_pure_neg_loc, input_names, indep_norm, global_log_indep)
    y_normed, _, _, _, _, _ = normalize_data(
        y_processed_data, y_crossing_loc, y_zero_loc, y_pure_neg_loc, y_names, dep_norm, global_log_dep)
    if dep_norm != 'none' and indep_norm != 'none': print("Data normalized.")
    X_x0_normed = X_normed[x0_idx]

    if no_lsa:
        lsa_obj = SensitivityPlots(
            pop=population, input_id2name=input_names, y_id2name=y_names, X=X_normed, y=y_normed, x0_idx=x0_idx,
            processed_data_y=y_processed_data, crossing_y=y_crossing_loc, z_y=y_zero_loc, pure_neg_y=y_pure_neg_loc,
            lsa_heatmap_values=lsa_heatmap_values)
        print("No exploration vector generated.")
        return lsa_obj

    plot_gini(X_normed, y_normed, X.shape[1], y.shape[1], input_names, y_names, inp_out_same, uniform, n_neighbors)
    neighbors_per_query = first_pass(X_normed, input_names, max_neighbors, beta, x0_idx, txt_file)
    neighbor_matrix, confound_matrix = clean_up(neighbors_per_query, X_normed, y_normed, X_x0_normed, input_names, y_names,
                                                n_neighbors, r_ceiling_val, p_baseline, confound_baseline,
                                                rel_start, repeat, save, save_format, txt_file, verbose, uniform, jupyter)
    if not jupyter:
        idxs_dict = {}
        for i in range(X.shape[1]):
            idxs_dict[i] = np.arange(y.shape[1])
        plot_neighbor_sets(X_normed, y_normed, idxs_dict, neighbors_per_query, neighbor_matrix, confound_matrix,
                           input_names, y_names, save, save_format)

    lsa_obj = SensitivityPlots(
        pop=population, neighbor_matrix=neighbor_matrix, query_neighbors=neighbors_per_query, input_id2name=input_names,
        y_id2name=y_names, X=X_normed, y=y_normed, x0_idx=x0_idx, processed_data_y=y_processed_data, crossing_y=y_crossing_loc,
        z_y=y_zero_loc, pure_neg_y=y_pure_neg_loc, n_neighbors=n_neighbors, confound_matrix=confound_matrix,
        lsa_heatmap_values=lsa_heatmap_values)

    coef_matrix, pval_matrix = get_coef_and_plot(
        X.shape[1], y.shape[1], neighbor_matrix, X_normed, y_normed, input_names, y_names, save, save_format, jupyter)
    failed_matrix = create_failed_search_matrix(
        X.shape[1], y.shape[1], neighbor_matrix, n_neighbors, lsa_heatmap_values)

    if txt_file is not None: txt_file.close()

    if input_str not in param_strings and population is not None:
        print("The exploration vector for the parameters was not generated because it was not the independent variable.")
    else:
        explore_dict = generate_explore_vector(n_neighbors, X.shape[1], y.shape[1], X[x0_idx], X_x0_normed,
                                               scaling, logdiff_array, logmin_array, diff_array, min_array,
                                               neighbor_matrix, indep_norm, perturb_all, perturb_range)
        save_perturbation_from_dict(explore_dict)

    lsa_obj.coef_matrix = coef_matrix
    lsa_obj.pval_matrix = pval_matrix

    if jupyter:
        return InteractivePlot(
            lsa_obj, coef_matrix, pval_matrix, input_names, y_names, failed_matrix, p_baseline, r_ceiling_val)
    else:
        InteractivePlot(
            lsa_obj, coef_matrix, pval_matrix, input_names, y_names, failed_matrix, p_baseline, r_ceiling_val)
        return lsa_obj


def interactive_colormap(lsa_obj, dep_norm, global_log_dep, processed_data_y, crossing_y, z_y, pure_neg_y, neighbor_matrix,
                         X_normed, y_normed, input_names, y_names, n_neighbors, lsa_heatmap_values, p_baseline,
                         r_ceiling_val, save, save_format):
    num_input = X_normed.shape[1]
    num_output = y_normed.shape[1]
    y_normed, _, _, _, _, _ = normalize_data(
        processed_data_y, crossing_y, z_y, pure_neg_y, y_names, dep_norm, global_log_dep)
    coef_matrix, pval_matrix = get_coef_and_plot(
        num_input, num_output, neighbor_matrix, X_normed, y_normed, input_names, y_names, save, save_format)
    failed_matrix = create_failed_search_matrix(
        num_input, num_output, neighbor_matrix, n_neighbors, lsa_heatmap_values)
    InteractivePlot(lsa_obj, coef_matrix, pval_matrix, input_names, y_names, failed_matrix, p_baseline, r_ceiling_val)

    return coef_matrix, pval_matrix, InteractivePlot

#------------------processing populationstorage and normalizing data

def pop_to_matrix(population, input_str, output_str, param_strings, obj_strings):
    """converts collection of individuals in PopulationStorage into a matrix for data manipulation

    :param population: PopulationStorage object
    :return: data: 2d array. rows = each data point or individual, col = parameters, then features
    """
    X_data = []
    y_data = []
    generation_array = population.history
    for generation in generation_array:
        for datum in generation:
            y_array = datum.objectives if output_str in obj_strings else datum.features
            y_data.append(y_array)
            if input_str in param_strings:
                x_array = datum.x
            elif input_str in obj_strings:
                x_array = datum.objectives
            else:
                x_array = datum.features
            X_data.append(x_array)
    return np.array(X_data), np.array(y_data)


def process_data(data):
    """need to log normalize parts of the data, so processing columns that are negative and/or have zeros is needed"""
    processed_data = np.copy(data)
    neg = list(set(np.where(data < 0)[1]))
    pos = list(set(np.where(data > 0)[1]))
    z = list(set(np.where(data == 0)[1]))
    crossing = [num for num in pos if num in neg]
    pure_neg = [num for num in neg if num not in pos]

    # transform data
    processed_data[:, pure_neg] *= -1
    # diff = np.max(data, axis=0) - np.min(data, axis=0)
    # diff[np.where(diff == 0)[0]] = 1.
    # magnitude = np.log10(diff)
    # offset = 10 ** (magnitude - 2)
    # processed_data[:, z] += offset[z]

    return processed_data, crossing, z, pure_neg


def x0_to_index(population, x0_string, X_data, input_str, param_strings, obj_strings):
    """
    from x0 string (e.g. 'best'), returns the respective array/data which contains
    both the parameter and output values
    """
    from nested.optimize_utils import OptimizationReport
    report = OptimizationReport(population)
    if x0_string == 'best':
        if input_str in param_strings:
            x0_x_array = report.survivors[0].x
        elif input_str in obj_strings:
            x0_x_array = report.survivors[0].objectives
        else:
            x0_x_array = report.survivors[0].features
    else:
        if input_str in param_strings:
            x0_x_array = report.specialists[x0_string].x
        elif input_str in obj_strings:
            x0_x_array = report.specialists[x0_string].objectives
        else:
            x0_x_array = report.specialists[x0_string].features
    index = np.where(X_data == x0_x_array)[0][0]
    return index


def normalize_data(processed_data, crossing, z, pure_neg, names, norm, global_log=None, magnitude_threshold=2):
    """normalize all data points. used for calculating neighborship

    :param processed_data: data has been transformed for the cols that need to be log-normalized such that the values
        can be logged
    :param crossing: list of column indices such that within the column, values cross 0
    :param z: list of column idx such that column has a 0
    :return: matrix of normalized values for parameters and features
    """
    # process_data DOES NOT process the columns (ie, parameters and features) that cross 0, because
    # that col will just be lin normed.
    warnings.simplefilter("ignore")

    data_normed = np.copy(processed_data)
    num_rows, num_cols = processed_data.shape

    min_array, diff_array = get_linear_arrays(processed_data)
    diff_array[np.where(diff_array == 0)[0]] = 1
    data_log_10 = np.log10(np.copy(processed_data))
    logmin_array, logdiff_array, logmax_array = get_log_arrays(data_log_10)

    scaling = []  # holds a list of whether the column was log or lin normalized (string)
    if norm == 'loglin':
        scaling = np.array(['log'] * num_cols)
        if global_log is True:
            scaling[np.where(logdiff_array < magnitude_threshold)[0]] = 'lin'
        else:
            n = logdiff_array.shape[0]
            scaling[np.where(logdiff_array[-int(n / 3):] < magnitude_threshold)[0]] = 'lin'
        scaling[crossing] = 'lin'; scaling[z] = 'lin'
        lin_loc = np.where(scaling == 'lin')[0]
        log_loc = np.where(scaling == 'log')[0]
        print("Normalization: %s." % list(zip(names, scaling)))
    elif norm == 'lin':
        scaling = np.array(['lin'] * num_cols)
        lin_loc = np.arange(num_cols)
        log_loc = []
    else:
        lin_loc = []
        log_loc = []

    data_normed[:, lin_loc] = np.true_divide((processed_data[:, lin_loc] - min_array[lin_loc]), diff_array[lin_loc])
    data_normed[:, log_loc] = np.true_divide((data_log_10[:, log_loc] - logmin_array[log_loc]), logdiff_array[log_loc])
    data_normed = np.nan_to_num(data_normed)
    data_normed[:, pure_neg] *= -1

    return data_normed, scaling, logdiff_array, logmin_array, diff_array, min_array

def get_linear_arrays(data):
    min_array = np.min(data, axis=0)
    max_array = np.max(data, axis=0)
    diff_array = abs(max_array - min_array)

    return min_array, diff_array

def get_log_arrays(data_log_10):
    logmin_array = np.min(data_log_10, axis=0)
    logmin_array[np.isnan(logmin_array)] = 0
    logmax_array = np.max(data_log_10, axis=0)
    logmax_array[np.isnan(logmax_array)] = 0
    logdiff_array = abs(logmax_array - logmin_array)

    return logmin_array, logdiff_array, logmax_array

#------------------independent variable importance

def add_user_knowledge(user_important_dict, y_name, imp):
    if user_important_dict is not None and y_name in user_important_dict.keys():
        for known_imp_input in user_important_dict[y_name]:
            if known_imp_input not in imp:
                imp.append(known_imp_input)

def accept_outliers(coef):
    IQR = iqr(coef)
    q3 = np.percentile(coef, 75)
    upper_baseline = q3 + 1.5 * IQR
    return set(np.where(coef > upper_baseline)[0])

#------------------consider all variables unimportant at first

def first_pass(X, input_names, max_neighbors, beta, x0_idx, txt_file):
    neighbor_arr = [[] for _ in range(X.shape[1])]
    x0_normed = X[x0_idx]
    X_dists = np.abs(X - x0_normed)
    output_text(
        "First pass: ",
        txt_file,
        True,
    )
    for i in range(X.shape[1]):
        neighbors = []
        unimp = [x for x in range(X.shape[1]) if x != i]
        X_dists_sorted = X_dists[X_dists[:, i].argsort()]

        for j in range(X.shape[0]):
            X_dist_sub = X_dists_sorted[j]
            rad = X_dists_sorted[j][i]
            if np.all(np.abs(X_dist_sub[unimp]) <= beta * rad):
                idx = np.where(X_dists == X_dists_sorted[j])[0][0]
                neighbors.append(idx)
            if len(neighbors) >= max_neighbors: break
        neighbor_arr[i] = neighbors
        max_dist = np.max(X_dists[neighbors][:, i])
        output_text(
            "    %s - %d neighbors found. Max query distance of %.8f." % (input_names[i], len(neighbors), max_dist),
            txt_file,
            True,
        )

    return neighbor_arr


def clean_up(neighbor_arr, X, y, X_x0, input_names, y_names, n_neighbors, r_ceiling_val, p_baseline,
             confound_baseline, rel_start, repeat, save, save_format, txt_file, verbose, uniform, jupyter):
    from diversipy import psa_select

    num_input = len(neighbor_arr)
    neighbor_matrix = np.empty((num_input, y.shape[1]), dtype=object)
    confound_matrix = np.empty((num_input, y.shape[1]), dtype=object)
    pdf = PdfPages("data/lsa/first_pass_colormaps.pdf") if save else None
    for i in range(num_input):
        nq = [x for x in range(num_input) if x != i]
        neighbor_orig = neighbor_arr[i].copy()
        confound_list = [[] for _ in range(y.shape[1])]
        for o in range(y.shape[1]):
            neighbors = neighbor_arr[i].copy()
            counter = 0
            current_confounds = None
            rel = rel_start
            while current_confounds is None or (rel > 0 and len(current_confounds) != 0 and len(neighbors) > n_neighbors):
                current_confounds = []
                rmv_list = []
                for i2 in nq:
                    r = abs(linregress(X[neighbors][:, i2], y[neighbors][:, o])[2])
                    pval = linregress(X[neighbors][:, i2], y[neighbors][:, o])[3]
                    if r >= confound_baseline and pval < p_baseline:
                        output_text(
                            "Iteration %d: For the set of neighbors associated with %s vs %s, %s was significantly "
                                "correlated with %s." % (counter, input_names[i], y_names[o], input_names[i2], y_names[o]),
                            txt_file,
                            verbose,
                        )
                        current_confounds.append(i2)
                        for n in neighbors:
                            if abs(X[n, i2] - X_x0[i2]) > rel * abs(X[n, i] - X_x0[i]):
                                if n not in rmv_list: rmv_list.append(n)
                for n in rmv_list: neighbors.remove(n)
                output_text(
                    "During iteration %d, for the pair %s vs %s, %d points were removed. %d remain." \
                        % (counter, input_names[i], y_names[o], len(rmv_list), len(neighbors)),
                    txt_file,
                    verbose,
                )
                if counter == 0:
                    confound_matrix[i][o] = current_confounds
                    confound_list[o] = current_confounds
                if not repeat: break
                rel -= (rel_start / 10.)
                counter += 1
            if repeat and len(current_confounds) != 0:
                neighbor_matrix[i][o] = []
            else:
                cleaned_selection = X[neighbors][:, i].reshape(-1, 1)
                if uniform and len(neighbors) >= n_neighbors and np.min(cleaned_selection) != np.max(cleaned_selection):
                    renormed = (cleaned_selection - np.min(cleaned_selection)) \
                               / (np.max(cleaned_selection) - np.min(cleaned_selection))
                    subset = psa_select(renormed, n_neighbors)
                    idx_nested = get_idx(renormed, subset)
                    neighbor_matrix[i][o] =  np.array(neighbors)[idx_nested]
                else:
                    neighbor_matrix[i][o] = neighbors
            if len(neighbors) < n_neighbors:
                output_text(
                    "----Clean up: %s vs %s - %d neighbor(s) remaining!" % (input_names[i], y_names[o], len(neighbors)),
                    txt_file,
                    True,
                )
        if not jupyter:
            plot_first_pass_colormap(neighbor_orig, X, y, input_names, y_names, input_names[i], confound_list, p_baseline,
                                     r_ceiling_val, pdf, save)
    if save: pdf.close()
    return neighbor_matrix, confound_matrix


def plot_neighbors(X_col, y_col, neighbors, input_name, y_name, title, save, save_format, close=True):
    a = X_col[neighbors]
    b = y_col[neighbors]
    plt.figure()
    plt.scatter(a, b, c=neighbors, cmap='viridis')
    plt.ylabel(y_name)
    plt.xlabel(input_name)
    # if all the points are in a hyper-local cluster, mpl's auto xlim and ylim are too large
    plt.xlim(np.min(a), np.max(a))
    plt.ylim(np.min(b), np.max(b))
    plt.title(title)
    if len(a) > 1:
        r = abs(linregress(a, b)[2])
        pval = linregress(a, b)[3]
        fit_fn = np.poly1d(np.polyfit(a, b, 1))
        plt.plot(a, fit_fn(a), color='red')
        plt.title("{} - Abs R = {:.2e}, p-val = {:.2e}".format(title, r, pval))
    if save: plt.savefig('data/lsa/%s_%s_vs_%s.%s' % (title, input_name, y_name, save_format), format=save_format)
    if close: plt.close()

def plot_neighbor_sets(X, y, idxs_dict, query_set, neighbor_matrix, confound_matrix, input_names, y_names, save, save_format,
                       close=True, plot_confounds=True):
    for i, output_list in idxs_dict.items():
        before = query_set[i]
        input_name = input_names[i]
        X_col = X[:, i]
        for o in output_list:
            after = neighbor_matrix[i][o]
            y_name = y_names[o]
            y_col = y[:, o]

            plt.figure()
            a = X_col[after]
            b = y_col[after]
            removed = list(set(before) - set(after))
            plt.scatter(a, b, color='purple', label="Selected points")
            if len(removed) != 0:
                alp = max(1. - .001 * len(removed), .1)
                plt.scatter(X_col[removed], y_col[removed], color='red', label="Removed points", alpha=alp)
                plt.legend()

            plt.ylabel(y_name)
            plt.xlabel(input_name)
            plt.xlim(np.min(X_col[before]), np.max(X_col[before]))
            plt.ylim(np.min(y_col[before]), np.max(y_col[before]))
            plt.title("{} vs {}".format(input_name, y_name))
            if len(a) > 1:
                r = abs(linregress(a, b)[2])
                pval = linregress(a, b)[3]
                fit_fn = np.poly1d(np.polyfit(a, b, 1))
                plt.plot(a, fit_fn(a), color='red')
                plt.title("{} vs {} - Abs R = {:.2e}, p-val = {:.2e}".format(input_name, y_name, r, pval))
            if save: plt.savefig('data/lsa/selected_points_%s_vs_%s.%s' % (input_name, y_name, save_format), format=save_format)
            if close: plt.close()
            if plot_confounds:
                for i2 in confound_matrix[i][o]:
                    plot_neighbors(X[:, i2], y[:, o], before, input_names[i2], y_name, "Clean up (query parameter = %s)"
                                   % input_names[i], save, save_format, close=close)


def plot_first_pass_colormap(neighbors, X, y, input_names, y_names, input_name, confound_list, p_baseline=.05, r_ceiling_val=None,
                             pdf=None, save=True, close=True):
    coef_matrix = np.zeros((X.shape[1], y.shape[1]))
    pval_matrix = np.zeros((X.shape[1], y.shape[1]))
    for i in range(X.shape[1]):
        for o in range(y.shape[1]):
            coef_matrix[i][o] = abs(linregress(X[neighbors, i], y[neighbors, o])[2])
            pval_matrix[i][o] = linregress(X[neighbors, i], y[neighbors, o])[3]
    fig, ax = plt.subplots(figsize=(16, 5))
    plt.title("Absolute R Coefficients - First pass of %s" % input_name)
    vmax = np.max(coef_matrix) if r_ceiling_val is None else r_ceiling_val
    cmap = plt.cm.GnBu
    cmap.set_under((0, 0, 0, 0))
    coef_matrix_masked = np.where(pval_matrix > p_baseline, 0, coef_matrix)
    ax.pcolor(coef_matrix_masked, cmap=cmap, vmin=0.01, vmax=vmax)
    annotate(coef_matrix_masked, vmax)
    set_centered_axes_labels(ax, input_names, y_names)
    outline_colormap(ax, confound_list)
    plt.xticks(rotation=-90)
    plt.yticks(rotation=0)
    plt.tight_layout()
    if save: pdf.savefig(fig)
    if close: plt.close()


def outline_colormap(ax, outline_list, fill=False):
    """
    :param ax: pyplot axis
    :param outline_list: 2d list. nested lists have the idxs of the input variables that were confounds
    :return:
    """
    patch_list = []
    for o, inp_list in enumerate(outline_list):
        for inp in inp_list:
            new_patch = Rectangle((o, inp), 1, 1, fill=fill, edgecolor='blue', lw=1.5) #idx from bottom left
            ax.add_patch(new_patch)
            patch_list.append(new_patch)
    return patch_list

def output_text(text, txt_file, verbose):
    if verbose: print(text)
    if txt_file is not None:
        txt_file.write(text)
        txt_file.write("\n")

def write_settings_to_file(input_str, output_str, x0_str, indep_norm, dep_norm, global_log_indep, global_log_dep, beta,
                           rel_start, confound_baseline, p_baseline, repeat, txt_file):
    txt_file.write("***************************************************" + u"\r\n")
    txt_file.write("Independent variable: %s\n" %input_str)
    txt_file.write("Dependent variable: %s\n" % output_str)
    txt_file.write("x0: %s\n" % x0_str)
    txt_file.write("Beta: %.2f\n" % beta)
    txt_file.write("Alpha: %.2f\n" % rel_start)
    txt_file.write("Repeats?: %s\n" % repeat)
    txt_file.write("Confound baseline: %.2f\n" % confound_baseline)
    txt_file.write("P-value threshold: %.2f\n" % p_baseline)
    txt_file.write("Independent variable normalization: %s\n" % indep_norm)
    if indep_norm == 'loglin':
        txt = 'global' if global_log_indep else 'local'
        txt_file.write("Independent variable log normalization: %s\n" % txt)
    txt_file.write("Dependent variable normalization: %s\n" % dep_norm)
    if dep_norm == 'loglin':
        txt = 'global' if global_log_dep else 'local'
        txt_file.write("Dependent variable log normalization: %s\n" % txt)
    txt_file.write("***************************************************\n")

#------------------lsa plot

def get_coef_and_plot(num_input, num_output, neighbor_matrix, X_normed, y_normed, input_names, y_names, save,
                      save_format, jupyter=False):
    """compute coefficients between parameter and feature based on linear regression. also get p-val
    coef will always refer to the R coefficient linear regression between param X and feature y

    :param num_input: int
    :param num_output: int
    :param neighbor_matrix: 2d array of lists which contain neighbor indices
    :param X_normed: 2d array of input vars normalized
    :param y_normed: 2d array of output vars normalized
    :return:
    """
    coef_matrix = np.zeros((num_input, num_output))
    pval_matrix = np.ones((num_input, num_output))

    for inp in range(num_input):
        for out in range(num_output):
            neighbor_array = neighbor_matrix[inp][out]
            if neighbor_array is not None and len(neighbor_array) > 0:
                selection = list(neighbor_array)
                X_sub = X_normed[selection, inp]

                coef_matrix[inp][out] = abs(linregress(X_sub, y_normed[selection, out])[2])
                pval_matrix[inp][out] = linregress(X_sub, y_normed[selection, out])[3]
                if not jupyter:
                    plot_neighbors(X_normed[:, inp], y_normed[:, out], neighbor_array, input_names[inp], y_names[out],
                                   "Final pass", save, save_format)
    return coef_matrix, pval_matrix

def create_failed_search_matrix(num_input, num_output, neighbor_matrix, n_neighbors, lsa_heatmap_values):
    """
    failure = not enough neighbors or confounded
    for each significant feature/parameter relationship identified, check if possible confounds are significant
    """
    failed_matrix = np.zeros((num_input, num_output))

    # not enough neighbors
    for param in range(num_input):
        for feat in range(num_output):
            if neighbor_matrix[param][feat] is None or len(neighbor_matrix[param][feat]) < n_neighbors:
                failed_matrix[param][feat] = lsa_heatmap_values['no_neighbors']
    return failed_matrix


# adapted from https://stackoverflow.com/questions/42976693/python-pick-event-for-pcolor-get-pandas-column-and-index-value
class InteractivePlot(object):
    def __init__(self, lsa_obj, coef_matrix, pval_matrix, input_names, y_names, sig_confounds, p_baseline=.05,
                 r_ceiling_val=None):
        self.lsa_obj = lsa_obj
        self.coef_matrix = coef_matrix
        self.pval_matrix = pval_matrix
        self.input_names = input_names
        self.y_names = y_names
        self.sig_confounds = sig_confounds
        self.p_baseline = p_baseline
        self.r_ceiling_val = r_ceiling_val
        self.data = None
        self.ax = None
        self.plot(coef_matrix, pval_matrix, input_names, y_names, sig_confounds, p_baseline, r_ceiling_val)

    def plot(self, coef_matrix, pval_matrix, input_names, y_names, sig_confounds, p_baseline=.05, r_ceiling_val=None):
        fig, ax = plt.subplots(figsize=(16, 5))
        plt.title("Absolute R Coefficients", y=1.11)
        vmax = min(.7, max(.1, np.max(coef_matrix))) if r_ceiling_val is None else r_ceiling_val

        cmap = plt.cm.GnBu
        cmap.set_under((0, 0, 0, 0))
        data = np.where(pval_matrix > p_baseline, 0, coef_matrix)
        data = np.where(sig_confounds != 0, 0, data)
        self.data = data
        self.ax = ax
        ax.pcolor(data, cmap=cmap, vmin=.01, vmax=vmax, picker=1)
        annotate(data, vmax)
        cmap = plt.cm.Greys
        cmap.set_under((0, 0, 0, 0))
        ax.pcolor(sig_confounds, cmap=cmap, vmin=.01, vmax=1)
        set_centered_axes_labels(ax, input_names, y_names)
        plt.xticks(rotation=-90)
        plt.yticks(rotation=0)
        create_custom_legend(ax)
        fig.canvas.mpl_connect('pick_event', self.onpick)
        plt.show()
        time.sleep(4)

    def onpick(self, event):
        x, y = np.unravel_index(event.ind, self.pval_matrix.shape)
        x, y = x[0], y[0] # idx from bottom left
        plot_dict = {self.input_names[x] : [self.y_names[y]]}
        outline = [[] for _ in range(len(self.y_names))]
        outline[y] = [x]

        patch = outline_colormap(self.ax, outline, fill=True)[0]
        #plt.pause(0.001)
        plt.draw()
        patch.remove()
        self.lsa_obj.plot_scatter_plots(plot_dict=plot_dict, save=False, show=True, plot_confounds=True)

def set_centered_axes_labels(ax, input_names, y_names):
    ax.set_yticks(np.arange(len(input_names)) + 0.5, minor=False)
    ax.set_xticks(np.arange(len(y_names)) + 0.5, minor=False)
    ax.set_yticklabels(input_names, minor=False)
    ax.set_xticklabels(y_names, minor=False)

#apparently matplotlib doesn't have a way to do this automatically like seaborn..
def annotate(data, vmax):
    for y in range(data.shape[0]):
        for x in range(data.shape[1]):
            if data[y, x] == 0: continue
            color = 'black' if vmax - data[y, x] > .45 * vmax else 'white'
            plt.text(x + 0.5, y + 0.5, '%.3f' % data[y, x], ha='center', va='center', color=color)

#from https://stackoverflow.com/questions/49223702/adding-a-legend-to-a-matplotlib-plot-with-a-multicolored-line
class HandlerColorLineCollection(HandlerLineCollection):
    def create_artists(self, legend, artist ,xdescent, ydescent, width, height, fontsize, trans):
        x = np.linspace(0,width,self.get_numpoints(legend)+1)
        y = np.zeros(self.get_numpoints(legend)+1)+height/2.-ydescent
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        lc = LineCollection(segments, cmap=artist.cmap, transform=trans)
        lc.set_array(x)
        lc.set_linewidth(artist.get_linewidth())
        return [lc]

def create_custom_legend(ax, colormap='GnBu'):
    nonsig = plt.Line2D((0, 1), (0, 0), color='white', marker='s', mec='k', mew=.5, linestyle='')
    no_neighbors = plt.Line2D((0, 1), (0, 0), color='#f3f3f3', marker='s', linestyle='')
    sig = LineCollection(np.zeros((2, 2, 2)), cmap=colormap, linewidth=5)
    labels = ["Not significant",  "Too few neighbors",  "Significant without confounds"]
    ax.legend([nonsig, no_neighbors, sig], labels, handler_map={sig: HandlerColorLineCollection(numpoints=4)},
              loc='upper center', bbox_to_anchor=(0.5, 1.12), ncol=4, fancybox=True, shadow=True)

#------------------

class SobolPlot(object):
    def __init__(self, total, total_conf, first_order, first_order_conf, second_order, input_names, y_names, err_bars):
        """

        :param total: 2d array
        :param total_conf: 2d array
        :param first_order: 2d array
        :param first_order_conf: 2d array
        :param second_order: dict. key = str (independent var name), val = 2d array
        :param input_names: list
        :param y_names: list
        """
        self.total = total
        self.total_conf = total_conf
        self.first_order = first_order
        self.first_order_conf = first_order_conf
        self.second_order = second_order
        self.y_names = y_names
        self.input_names = input_names
        self.ax = None
        self.acceptable_columns = None
        self.err_bars = err_bars

        self.plot()

    def plot(self):
        fig, ax = plt.subplots()
        plt.title("Total effects")
        # if a feature/objective does not vary at all, the column is a row of NaNs, which messes up
        # onpick event clicking
        self.acceptable_columns = [x for x in range(self.total.shape[1]) if not np.isnan(self.total[:, x]).any()]
        self.ax = ax
        ax.pcolor(self.total[:, self.acceptable_columns], cmap='GnBu', picker=1)
        annotate(self.total[:, self.acceptable_columns], np.max(self.total))
        set_centered_axes_labels(ax, self.input_names, np.array(self.y_names)[self.acceptable_columns])
        plt.xticks(rotation=-90)
        plt.yticks(rotation=0)
        fig.canvas.mpl_connect('pick_event', self.onpick)
        plt.show()
        plt.close()

    def plot_second_order_effects(self, output_idx):
        fig, ax = plt.subplots()
        plt.title("Second-order effects on {}".format(self.y_names[output_idx]))

        data = self.second_order[self.y_names[output_idx]]
        ax.pcolor(data, cmap='GnBu')
        annotate(data, np.max(data))
        set_centered_axes_labels(ax, self.input_names, self.input_names)
        plt.xticks(rotation=-90)
        plt.yticks(rotation=0)

    def plot_first_order_effects(self, output_idx, err_bars=True):
        fig, ax = plt.subplots()
        width = .35
        total_err = self.total_conf[:, output_idx] if err_bars else np.zeros((len(self.input_names),))
        first_err = self.first_order_conf[:, output_idx] if err_bars else np.zeros((len(self.input_names),))
        capsize = 2. if err_bars else 0.

        rect1 = ax.bar(np.arange(len(self.input_names)), self.total[:, output_idx], width, align='center',
                       yerr=total_err, label="Total effects", capsize=capsize)
        rect2 = ax.bar(np.arange(len(self.input_names)) + width, self.first_order[:, output_idx], width, align='center',
                       yerr=first_err, label="First order effects", capsize=capsize)
        plt.legend()
        autolabel(rect1, ax)
        autolabel(rect2, ax)
        plt.title("First order vs total effects on {}".format(self.y_names[output_idx]))
        plt.xticks(np.arange(len(self.input_names)) + width / 2, self.input_names, rotation=-90)
        plt.ylabel('Effect')
        plt.yticks(rotation=0)

    def onpick(self, event):
        _, y = np.unravel_index(event.ind, (self.total.shape[0], len(self.acceptable_columns)))
        y = y[0] # idx from bottom left
        outline = [[] for _ in range(len(self.y_names))]
        outline[y] = [i for i in range((len(self.input_names)))]

        patch_list = outline_colormap(self.ax, outline, fill=True)
        plt.pause(0.001)
        plt.draw()
        for patch in patch_list:
            patch.remove()
        self.plot_first_order_effects(y, self.err_bars)
        self.plot_second_order_effects(y)
        plt.show()

# https://matplotlib.org/3.1.1/gallery/lines_bars_and_markers/barchart.html
def autolabel(rects, ax):
    """Attach a text label above each bar in *rects*, displaying its height."""
    for rect in rects:
        height = rect.get_height()
        ax.annotate('{:.2f}'.format(height),
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),  # 3 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom')


#------------------plot importance via ensemble

def plot_gini(X, y, num_input, num_output, input_names, y_names, inp_out_same, uniform, n_neighbors):
    """

    :param X:
    :param y:
    :param num_input:
    :param num_output:
    :param input_names:
    :param y_names:
    :param inp_out_same:
    :param uniform:
    :param n_neighbors:
    :return:
    """
    from diversipy import psa_select

    num_trees = 50
    tree_height = 25
    mtry = max(1, int(.1 * len(input_names)))
    # the sum of feature_importances_ is 1, so the baseline should be relative to num_input
    # the below calculation is pretty ad hoc and based fitting on (20, .1), (200, .05), (2000, .01); (num_input, baseline)
    # baseline = 0.15688 - 0.0195433 * np.log(num_input)
    # if baseline < 0: baseline = .005
    #important_inputs = [[] for _ in range(num_output)]
    input_importances = np.zeros((num_input, num_output))

    # create a forest for each feature. each independent var is considered "important" if over the baseline
    for i in range(num_output):
        rf = ExtraTreesRegressor(random_state=0, max_features=mtry, max_depth=tree_height, n_estimators=num_trees)
        Xi = X[:, [x for x in range(num_input) if x != i]] if inp_out_same else X
        output_vals = y[:, i].reshape(-1, 1)
        if uniform and np.min(output_vals) != np.max(output_vals):
            renormed = (output_vals - np.min(output_vals)) / (np.max(output_vals) - np.min(output_vals))
            subset = psa_select(renormed, n_neighbors)
            idx = get_idx(renormed, subset)
            Xi = Xi[idx]
            yi = y[idx, i]
        else:
            yi = output_vals
        rf.fit(Xi, yi)

        # imp_loc = list(set(np.where(rf.feature_importances_ >= baseline)[0]) | accept_outliers(rf.feature_importances_))
        feat_imp = rf.feature_importances_
        if inp_out_same:
            # imp_loc = [x + 1 if x >= i else x for x in imp_loc]
            feat_imp = np.insert(feat_imp, i, np.NaN)
        input_importances[:, i] = feat_imp
        # important_inputs[i] = list(input_names[imp_loc])

    fig, ax = plt.subplots()
    hm = sns.heatmap(input_importances, cmap='cool', fmt=".2f", linewidths=1, ax=ax, cbar=True, annot=True)
    hm.set_xticklabels(y_names)
    hm.set_yticklabels(input_names)
    plt.xticks(rotation=-90)
    plt.yticks(rotation=0)
    plt.title('Gini importances')
    plt.show()

#------------------user input

def prompt_float(prompt):
    var = None
    while var is not float:
        try:
            var = input(prompt)
            return float(var)
        except ValueError:
            print('Please enter a float.')

def prompt_indiv(valid_names):
    user_input = ''
    while user_input != 'best' and user_input not in valid_names:
        print('Valid strings for x0: %s.' % (['best'] + valid_names))
        user_input = (input('Specify x0: ')).lower()

    return user_input

def prompt_norm(variable_string):
    user_input = ''
    while user_input.lower() not in ['lin', 'loglin', 'none']:
        user_input = input('How should %s variables be normalized? Accepted answers: lin/loglin/none: ' % variable_string)
    return user_input.lower()

def prompt_global_vs_local(variable_str):
    user_input = ''
    while user_input.lower() not in ['g', 'global', 'l', 'local']:
        user_input = input('For determining whether a%s variable is log normalized, should its value across all '
                           'generations be examined or only the last third? Accepted answers: local/global: '
                           % variable_str)
    return user_input.lower() in ['g', 'global']

def prompt_input():
    user_input = ''
    while user_input.lower() not in ['f', 'o', 'feature', 'objective', 'parameter', 'p', 'features', 'objectives',
                                     'parameters']:
        user_input = input('What is the independent variable (features/objectives/parameters)?: ')
    return user_input.lower()

def prompt_output():
    user_input = ''
    while user_input.lower() not in ['f', 'o', 'feature', 'objective', 'features', 'objectives']:
        user_input = input('What is the the dependent variable (features/objectives)?: ')
    return user_input.lower()

def get_variable_names(population, input_str, output_str, obj_strings, feat_strings, param_strings):
    if input_str in obj_strings:
        input_names = population.objective_names
    elif input_str in feat_strings:
        input_names = population.feature_names
    elif input_str in param_strings:
        input_names = population.param_names
    else:
        raise RuntimeError('SA: input variable "%s" is not recognized' % input_str)

    if output_str in obj_strings:
        y_names = population.objective_names
    elif output_str in feat_strings:
        y_names = population.feature_names
    elif output_str in param_strings:
        raise RuntimeError("SA: parameters are currently not an acceptable dependent variable.")
    else:
        raise RuntimeError('SA: output variable "%s" is not recognized' % output_str)
    return np.array(input_names), np.array(y_names)

def check_user_importance_dict_correct(dct, input_names, y_names):
    incorrect_strings = []
    for y_name in dct.keys():
        if y_name not in y_names: incorrect_strings.append(y_name)
    for _, known_important_inputs in dct.items():
        if not isinstance(known_important_inputs, list):
            raise RuntimeError('For the known important variables dictionary, the value must be a list, even if '
                               'the list contains only one variable.')
        for name in known_important_inputs:
            if name not in input_names: incorrect_strings.append(name)
    if len(incorrect_strings) > 0:
        raise RuntimeError('Some strings in the dictionary are incorrect. Are the keys dependent variables (string) '
                           'and the values independent variables (list of strings)? These inputs have errors: %s.'
                           % incorrect_strings)

def check_save_format_correct(save_format):
    accepted = ['png', 'pdf', 'svg']
    if save_format not in accepted:
        raise RuntimeError("For the save format for the plots, %s is not an accepted string. Accepted strings are: "
                           "%s." % (save_format, accepted))

def check_data_format_correct(population, X, y):
    if (population is not None and (X is not None or y is not None)) \
            or (population is None and (X is None or y is None)):
        raise RuntimeError("Please either give one PopulationStorage object or a pair of arrays.")

#------------------explore vector

def denormalize(scaling, unnormed_vector, param, logdiff_array, logmin_array, diff_array, min_array):
    if scaling[param] == 'log':
        unnormed_vector = np.power(10, (unnormed_vector * logdiff_array[param] + logmin_array[param]))
    else:
        unnormed_vector = unnormed_vector * diff_array[param] + min_array[param]

    return unnormed_vector

def create_perturb_matrix(X_x0, n_neighbors, input, perturbations):
    """
    :param X_best: x0
    :param n_neighbors: int, how many perturbations were made
    :param input: int, idx for independent variable to manipulate
    :param perturbations: array
    :return:
    """
    perturb_matrix = np.tile(np.array(X_x0), (n_neighbors, 1))
    perturb_matrix[:, input] = perturbations
    return perturb_matrix

def generate_explore_vector(n_neighbors, num_input, num_output, X_x0, X_x0_normed, scaling, logdiff_array, logmin_array,
                            diff_array, min_array, neighbor_matrix, norm_search, perturb_all, perturb_range=.1):
    """
    figure out which X/y pairs need to be explored: non-sig or no neighbors
    generate n_neighbor points around best point. perturb just POI... 5% each direction

    :return: dict, key=param number (int), value=list of arrays
    """
    explore_dict = {}
    if n_neighbors % 2 == 1: n_neighbors += 1
    perturb_dist = perturb_range / 2

    if norm_search == 'none':
        print("The explore vector will perturb the parameter of interest by at most %.2f units in either direction."
              % perturb_dist)
        print("If this is not desired, please restart sensitivity analysis and set the normalization for the parameters.")

    for inp in range(num_input):
        for output in range(num_output):
            if perturb_all or neighbor_matrix[inp][output] is None or len(neighbor_matrix[inp][output]) < n_neighbors:
                upper = perturb_dist * np.random.random_sample((int(n_neighbors / 2),)) + X_x0_normed[inp]
                lower = perturb_dist * np.random.random_sample((int(n_neighbors / 2),)) + X_x0_normed[inp] - perturb_dist
                unnormed_vector = np.concatenate((upper, lower), axis=0)

                perturbations = unnormed_vector if norm_search is 'none' else denormalize(
                    scaling, unnormed_vector, inp, logdiff_array, logmin_array, diff_array, min_array)
                perturb_matrix = create_perturb_matrix(X_x0, n_neighbors, inp, perturbations)
                explore_dict[inp] = perturb_matrix

    return explore_dict

def save_perturbation_from_dict(perturb_dict):
    full_path = 'data/{}{}{}{}{}{}_perturbations.hdf5'.format(*time.localtime())
    counter = 0
    with h5py.File(full_path, 'a') as f:
        for param_id in perturb_dict:
            for i in range(len(perturb_dict[param_id])):
                f.create_group(str(counter))
                f[str(counter)]['x'] = perturb_dict[param_id][i]
                counter += 1

#------------------

class SensitivityPlots(object):
    """"
    allows for re-plotting after sensitivity analysis has been conducted
    """
    def __init__(self, pop=None, neighbor_matrix=None, coef_matrix=None, pval_matrix=None, query_neighbors=None,
                 confound_matrix=None, input_id2name=None, y_id2name=None, X=None, y=None, x0_idx=None, processed_data_y=None,
                 crossing_y=None, z_y=None, pure_neg_y=None, n_neighbors=None, lsa_heatmap_values=None):
        self.neighbor_matrix = neighbor_matrix
        self.query_neighbors = query_neighbors
        self.confound_matrix = confound_matrix
        self.coef_matrix = coef_matrix
        self.pval_matrix = pval_matrix
        self.X = X
        self.y = y
        self.x0_idx = x0_idx
        self.lsa_heatmap_values = lsa_heatmap_values
        self.summed_obj = sum_objectives(pop, X.shape[0]) if pop is not None else None

        self.processed_data_y = processed_data_y
        self.crossing_y = crossing_y
        self.z_y = z_y
        self.pure_neg_y = pure_neg_y
        self.n_neighbors = n_neighbors

        self.input_names = input_id2name
        self.y_names = y_id2name
        self.input_name2id = {}
        self.y_name2id = {}

        for i, name in enumerate(input_id2name): self.input_name2id[name] = i
        for i, name in enumerate(y_id2name): self.y_name2id[name] = i


    def plot_all(self, show=True, save=False, save_format='png', plot_confounds=True):
        print("Plotting R values based on naive point selection")
        self.first_pass_colormap(save=save, show=show)
        if plot_confounds:
            print("Plotting all scatter plots")
        else:
            print("Plotting main scatter plots")
        self.plot_scatter_plots(show=show, save=save, save_format=save_format)
        print("Plotting R values based on the final point selection")
        self.plot_final_colormap()


    def plot_vs_filtered(self, input_name, y_name, x_axis=None, y_axis=None):
        """
        plots the set of points associated with a specific independent/dependent variable pair.

        :param input_name: string. independent variable name.
        :param y_name: string. dependent variable name.
        :param x_axis: string or None. default is None. if None, the x axis of the plot is the same as input_name
        :param y_axis: string or None. default is None. if None, the y axis of the plot is the same as y_name
        :return:
        """
        if self.neighbor_matrix is None:
            raise RuntimeError("SA was not run. Please use plot_vs_unfiltered() instead.")
        if (x_axis is None or y_axis is None) and x_axis != y_axis:
            raise RuntimeError("Please specify both or none of the axes.")

        input_id = get_var_idx(input_name, self.input_name2id)
        output_id, = get_var_idx(y_name, self.y_name2id)
        if x_axis is not None:
            x_id, input_bool_x = get_var_idx_agnostic(x_axis, self.input_name2id, self.y_name2id)
            y_id, input_bool_y = get_var_idx_agnostic(y_axis, self.input_name2id, self.y_name2id)
        else:
            x_id = input_id
            y_id = output_id
            input_bool_x = True
            input_bool_y = False

        neighbor_indices = self.neighbor_matrix[input_id][output_id]
        if neighbor_indices is None or len(neighbor_indices) <= 1:
            print("No neighbors-- nothing to show.")
        else:
            a = self.X[neighbor_indices, x_id] if input_bool_x else self.y[neighbor_indices, x_id]
            b = self.X[neighbor_indices, y_id] if input_bool_y else self.y[neighbor_indices, y_id]
            plt.scatter(a, b)
            plt.scatter(self.X[self.x0_idx, x_id], self.y[self.x0_idx, y_id], color='red', marker='+')
            fit_fn = np.poly1d(np.polyfit(a, b, 1))
            plt.plot(a, fit_fn(a), color='red')

            plt.title("{} vs {} with p-val of {:.2e} and R coef of {:.2e}.".format(
                input_name, y_name, self.pval_matrix[input_id][output_id], self.coef_matrix[input_id][output_id]))

            plt.xlabel(input_name)
            plt.ylabel(y_name)
            plt.show()


    def plot_vs_unfiltered(self, x_axis, y_axis, num_models=None, last_third=False):
        """
        plots any two variables against each other. does not use the filtered set of points gathered during
        sensitivity analysis.

        :param x_axis: string. name of in/dependent variable
        :param y_axis: string. name of in/dependent variable
        :param num_models: int or None. if None, plot all models. else, plot the last num_models.
        :param last_third: bool. if True, use only the values associated with the last third of the optimization
        """
        x_id, input_bool_x = get_var_idx_agnostic(x_axis, self.input_name2id, self.y_name2id)
        y_id, input_bool_y = get_var_idx_agnostic(y_axis, self.input_name2id, self.y_name2id)

        if num_models is not None:
            num_models = int(num_models)
            x = self.X[-num_models:, x_id] if input_bool_x else self.y[-num_models:, x_id]
            y = self.X[-num_models:, y_id] if input_bool_y else self.y[-num_models:, y_id]
            plt.scatter(x, y, c=self.summed_obj[-num_models:], cmap='viridis_r')
            plt.title("Last {} models.".format(num_models))
        elif last_third:
            m = int(self.X.shape[0] / 3)
            x = self.X[-m:, x_id] if input_bool_x else self.y[-m:, x_id]
            y = self.X[-m:, y_id] if input_bool_y else self.y[-m:, y_id]
            plt.scatter(x, y, c=self.summed_obj[-m:], cmap='viridis_r')
            plt.title("Last third of models.")
        else:
            x = self.X[:, x_id] if input_bool_x else self.y[:, x_id]
            y = self.X[:, y_id] if input_bool_y else self.y[:, y_id]
            plt.scatter(x, y, c=self.summed_obj, cmap='viridis_r')
            plt.title("All models.")

        plt.scatter(self.X[self.x0_idx, x_id], self.y[self.x0_idx, y_id], color='red', marker='+')
        plt.colorbar().set_label("Summed objectives")
        plt.xlabel(x_axis)
        plt.ylabel(y_axis)
        plt.show()


    def first_pass_colormap(self, inputs=None, p_baseline=.05, r_ceiling_val=None, save=True, show=False):
        """
        there is a unique set of points for each of the independent variables during the first pass. for each of the sets
         specified, the linear relationship between each independent and dependent variable will be plotted.

        :param inputs: a list of strings of input variable names, or None. if None, the colormap for each variable
            will be plotted.
        :param p_baseline: a float from 0 to 1. threshold for statistical significance
        :param r_ceiling_val: a float from 0 to 1, or None. if specified, all of the colormaps plotted will have the
            same upper bound
        :param save: bool
        """
        if self.query_neighbors is None:
            raise RuntimeError("SA was not run.")
        pdf = PdfPages("data/lsa/first_pass_colormaps.pdf") if save else None

        if inputs is None:
            query = [x for x in range(len(self.input_names))]
        else:
            query = []
            for inp in inputs:
                try:
                    query.append(np.where(self.input_names == inp)[0][0])
                except:
                    raise RuntimeError("One of the inputs specified is not correct. Valid inputs are: %s." % self.input_names)
        for i in query:
            plot_first_pass_colormap(self.query_neighbors[i], self.X, self.y, self.input_names, self.y_names,
                                     self.input_names[i], self.confound_matrix[i], p_baseline, r_ceiling_val, pdf, save,
                                     not show)
        if save: pdf.close()
        if show: plt.show()


    def plot_final_colormap(self, dep_norm='none', global_log_dep=None, r_ceiling_val=.7, p_baseline=.05):
        """
        plots the final colormap of absolute R values that one sees at the end of sensitivity analysis.

        :param dep_norm: string. specifies how the dependent variable will be normalized. default is 'none.' other accepted
           strings are 'loglin' and 'lin.'
        :param global_log_dep: string or None. if dep_norm is 'loglin,' then the user can specify if the normalization
           should be done globally or locally. accepted strings are 'local' and 'global.'
        :param
        """
        if self.neighbor_matrix is None:
            raise RuntimeError("SA was not done.")
        interactive_colormap(self, dep_norm, global_log_dep, self.processed_data_y, self.crossing_y, self.z_y, self.pure_neg_y,
                             self.neighbor_matrix, self.X, self.y, self.input_names, self.y_names, self.n_neighbors,
                             self.lsa_heatmap_values, p_baseline, r_ceiling_val, save=False, save_format='png')


    def plot_scatter_plots(self, plot_dict=None, show=True, save=True, plot_confounds=False, save_format='png'):
        idxs_dict = defaultdict(list)
        if plot_dict is not None: idxs_dict = convert_user_query_dict(plot_dict, self.input_names, self.y_names)
        if plot_dict is None:
            for i in range(len(self.input_names)):
                idxs_dict[i] =  range(len(self.y_names))

        plot_neighbor_sets(self.X, self.y, idxs_dict, self.query_neighbors, self.neighbor_matrix, self.confound_matrix,
                           self.input_names, self.y_names, save, save_format, not show, plot_confounds)
        if show: plt.show()


    def first_pass_scatter_plots(self, plot_dict=None, show=True, save=True, save_format='png'):
        """
        plots the scatter plots during the naive search.

        :param plot_dict: dict or None. the key is a string (independent variable) and the value is a list of strings (of
            dependent variables). if None, all of the plots are plotted
        :param show: bool. if False, the plot does not appear, but it may be saved if save is True
        :param save: bool
        :param save_format: string: 'png,' 'svg,' or 'pdf.'
        """
        idxs_dict = defaultdict(list)
        if plot_dict is not None: idxs_dict = convert_user_query_dict(plot_dict, self.input_names, self.y_names)
        if plot_dict is None:
            for i in range(len(self.input_names)):
                idxs_dict[i] =  range(len(self.y_names))
        for i, output_list in idxs_dict.items():
            for o in output_list:
                neighbors = self.query_neighbors[i]
                plot_neighbors(self.X[:, i], self.y[:, o], neighbors, self.input_names[i], self.y_names[o],
                               "First pass", save=save, save_format=save_format, close=not show)


    def clean_up_scatter_plots(self, plot_dict=None, show=True, save=True, save_format='png'):
        """
        plots the relationships after the clean-up search. if there were confounds in the naive set of neighbors,
            the relationship between the confound and the dependent variable of interest are also plotted.

        :param plot_dict: dict or None. the key is a string (independent variable) and the value is a list of strings (of
            dependent variables). if None, all of the plots are plotted
        :param show: bool. if False, the plot does not appear, but it may be saved if save is True
        :param save: bool
        :param save_format: string: 'png,' 'svg,' or 'pdf.'
        """
        idxs_dict = defaultdict(list)
        if self.confound_matrix is None:
            raise RuntimeError('SA was not run.')
        if plot_dict is not None: idxs_dict = convert_user_query_dict(plot_dict, self.input_names, self.y_names)
        if plot_dict is None:
            for i in range(len(self.input_names)):
                idxs_dict[i] = range(len(self.y_names))
        for i, output_list in idxs_dict.items():
            for o in output_list:
                neighbors = self.query_neighbors[i]
                confounds = self.confound_matrix[i][o]
                if confounds is None:
                    print("%s vs. %s was not confounded." % (self.input_names[i], self.y_names[o]))
                else:
                    for confound in confounds:
                        plot_neighbors(self.X[:, confound], self.y[:, o], neighbors, self.input_names[confound],
                                       self.y_names[o], "Clean up (query parameter = %s)" % (self.input_names[i]),
                                       save, save_format, not show)
                final_neighbors = self.neighbor_matrix[i][o]
                plot_neighbors(self.X[:, i], self.y[:, o], final_neighbors, self.input_names[i],
                               self.y_names[o], "Final", save, save_format, not show)


    def return_filtered_data(self, input_name, y_name):
        input_id = get_var_idx(input_name, self.input_name2id)
        y_id = get_var_idx(y_name, self.y_name2id)
        neighbor_indices = self.neighbor_matrix[input_id][y_id]
        if neighbor_indices is None or len(neighbor_indices) <= 1:
            raise RuntimeError("No neighbors were found for this pair.")
        return self.X[neighbor_indices], self.y[neighbor_indices]


def get_var_idx(var_name, var_dict):
    try:
        idx = var_dict[var_name]
    except:
        raise RuntimeError('The provided variable name %s is incorrect. Valid choices are: %s.'
                           % (var_name, list(var_dict.keys())))
    return idx

def get_var_idx_agnostic(var_name, input_dict, output_dict):
    if var_name not in input_dict.keys() and var_name not in output_dict.keys():
        raise RuntimeError('The provided variable name %s is incorrect. Valid choices are: %s.'
                           % (var_name, list(input_dict.keys()) + list(output_dict.keys())))
    elif var_name in input_dict.keys():
        return input_dict[var_name], True
    elif var_name in output_dict.keys():
        return output_dict[var_name], False

def sum_objectives(pop, n):
    summed_obj = np.zeros((n,))
    counter = 0
    generation_array = pop.history
    for generation in generation_array:
        for datum in generation:
            summed_obj[counter] = sum(abs(datum.objectives))
            counter += 1
    return summed_obj

def convert_user_query_dict(dct, input_names, y_names):
    res = defaultdict(list)
    incorrect_input = []
    for k, li in dct.items():
        valid = True
        if k not in input_names:
            incorrect_input.append(k)
            valid = False
        for output_name in li:
            if output_name.lower() != 'all' and output_name not in y_names:
                incorrect_input.append(output_name)
            elif valid:
                if output_name.lower == 'all':
                    res[np.where(input_names == k)[0][0]] = [x for x in range(len(input_names))]
                else:
                    res[np.where(input_names == k)[0][0]].append(np.where(y_names == output_name)[0][0])

    if len(incorrect_input) > 0:
        raise RuntimeError("Dictionary is incorrect. The key must be a string (independent variable) and the value a "
                           "list of strings (dependent variables). Incorrect inputs were: %s. " % incorrect_input)
    return res

#----------------------------------------run with given parameter values

def sensitivity_analysis_from_hdf5(hdf5_file_path, config_file_path, n_neighbors=None, feat=True):
    """

    :param hdf5_file_path: str
    :param config_file_path: str
    :param n_neighbors: int or None. if None, the function tries to figure it out by itself
    :param feat: bool. if true, perform analysis on features rather than objectives
    :return:
    """
    from nested.utils import read_from_yaml

    yaml_dict = read_from_yaml(config_file_path)
    input_names = yaml_dict['param_names']
    output_names = yaml_dict['feature_names'] if feat else yaml_dict['objective_names']

    pval_matrix = None
    coef_matrix = None
    y_str = 'features' if feat else 'objectives'
    X, y = read_hdf5_file(hdf5_file_path, 'x', y_str)
    if X is None:
        raise RuntimeError("The .hdf5 file is missing feature/objective values.")
    if n_neighbors is None:
        n_neighbors = calculate_n_neighbors(X)

    subset_input_names = []
    for i in range(int(X.shape[0] / n_neighbors)):
        pval_li = []
        coef_li = []
        inp = np.where(X[i * n_neighbors] - X[i * n_neighbors + 1] != 0)[0][0]
        subset_input_names.append(input_names[inp])
        for j in range(len(output_names)):
            lin_reg = linregress(X[i : (i + 1) * n_neighbors][:, inp], y[i : (i + 1) * n_neighbors][:, j])
            coef_li.append(abs(lin_reg[2]))
            pval_li.append(lin_reg[3])
        coef_matrix = np.array(coef_li) if coef_matrix is None else np.vstack((coef_matrix, np.array(coef_li)))
        pval_matrix = np.array(pval_li) if pval_matrix is None else np.vstack((pval_matrix, np.array(pval_li)))
    plot_r_hm(pval_matrix, coef_matrix, subset_input_names, output_names)


def sobol(config_file_path, hdf5_file_path, feat=True, save=True, err_bars=True):
    from nested.utils import read_from_yaml
    from SALib.analyze import sobol

    bounds = None
    yaml_dict = read_from_yaml(config_file_path)
    input_names = yaml_dict['param_names']
    output_names = yaml_dict['feature_names'] if feat else yaml_dict['objective_names']
    for name, bound in yaml_dict['bounds'].items():
        bounds = np.array(bound) if bounds is None else np.vstack((bounds, np.array(bound)))

    problem = {
        'num_vars' : len(input_names),
        'names' : input_names,
        'bounds' : bounds,
    }

    txt_path = 'data/{}{}{}{}{}{}_sobol_analysis.txt'.format(*time.localtime())
    total_effects = np.zeros((len(input_names), len(output_names)))
    total_effects_conf = np.zeros((len(input_names), len(output_names)))
    first_order = np.zeros((len(input_names), len(output_names)))
    first_order_conf = np.zeros((len(input_names), len(output_names)))
    second_order = {}
    X = read_hdf5_file(hdf5_file_path, 'x')
    y = read_hdf5_file(hdf5_file_path, 'features') if feat else read_hdf5_file(hdf5_file_path, 'objectives')

    if y is None:
        raise RuntimeError("Please evaluate the model using the parameter values provided in the .hdf5 file. The .hdf5 "
                           "file is missing feature/objective values.")

    if X.shape[0] != y.shape[0]:
        raise RuntimeError("The number of points in parameter space does not match that of the feature/objective space. "
                           "If there is a failure condition for your model, please turn off the condition or adjust "
                           "the bounds of your parameters such that it does not reach it.")

    for o in range(y.shape[1]):
        print("---------------Dependent variable {}---------------".format(output_names[o]))
        Si = sobol.analyze(problem, y[:, o], print_to_console=True)
        total_effects[:, o] = Si['ST']
        total_effects_conf[:, o] = Si['ST_conf']
        first_order[:, o] = Si['S1']
        first_order_conf[:, o] = Si['S1_conf']
        second_order[output_names[o]] = Si['S2']
        if save:
            write_sobol_dict_to_txt(txt_path, Si, output_names[o], input_names)

    SobolPlot(total_effects, total_effects_conf, first_order, first_order_conf, second_order, input_names, output_names,
              err_bars)


def read_hdf5_file(file_path, first_attr, second_attr=None):
    """
    if second_attr is specified, only read in first_arr/second_attr if both are present. e.g., if first_attr
        is 'x' and second_attr is 'feat', if a set of values of x fails, the corresponding features are not calculated.
        in this case, discard those x values.

    :param file_path: str
    :param first_attr: str, 'x', 'features', or 'objectives'
    :param second_attr: str or None, 'features', or 'objectives'
    :return: 2d array(s)
    """
    a = None
    b = None
    with h5py.File(file_path, 'r') as f:
        # f.keys() arranged 0 -> 1 -> 10... etc instead of 0 -> 1 -> 2...
        n = len(list(f.keys()))
        for gen_id in range(n):
            if second_attr is None and first_attr in f[str(gen_id)].keys():
                a = f[str(gen_id)][first_attr] if a is None else np.vstack((a, f[str(gen_id)][first_attr]))
            else:
                if first_attr in f[str(gen_id)].keys() and second_attr in f[str(gen_id)].keys():
                    a = f[str(gen_id)][first_attr] if a is None else np.vstack((a, f[str(gen_id)][first_attr]))
                    b = f[str(gen_id)][second_attr] if b is None else np.vstack((b, f[str(gen_id)][second_attr]))

    if second_attr is None:
        return a
    else:
        return a, b

def write_hdf5_file(file_path, features, objectives):
    """

    :param file_path: str
    :param features: dict
    :param objectives: dict
    """
    with h5py.File(file_path, 'a') as f:
        for i in range(len(features)):
            # assumes each dict in list returned by evaluate_population has keys in the same order
            if 'failed' not in features[i].keys():
                f[str(i)]['features'] = dict_vals_to_arr(features[i])
                f[str(i)]['objectives'] = dict_vals_to_arr(objectives[i])

def dict_vals_to_arr(d):
    li = []
    for k, v in d.items():
        li.append(v)
    return li

def calculate_n_neighbors(X):
    counter = 1
    for j in range(1, X.shape[0]):
        perturb = np.where(X[0] - X[j] != 0)[0]
        if len(perturb) != 1:
            return counter
        counter += 1
    return X.shape[0]

def write_sobol_dict_to_txt(path, Si, y_name, input_names):
    """
    the dict returned from sobol.analyze is organized in a particular way
    :param path: str
    :param Si: dict
    :param y_name: str
    :param input_names: list of str
    :return:
    """
    with open(path, 'a') as f:
        f.write("---------------Dependent variable %s---------------\n" % y_name)
        f.write("Parameter S1 S1_conf ST ST_conf\n")
        for i in range(len(input_names)):
            f.write("%s %.6f %.6f %.6f %.6f\n"
                    % (input_names[i], Si['S1'][i], Si['S1_conf'][i], Si['ST'][i], Si['ST_conf'][i]))
        f.write("\nParameter_1 Parameter_2 S2 S2_conf\n")
        for i in range(len(input_names) - 1):
            for j in range(i + 1, len(input_names)):
                f.write("%s %s %.6f %.6f\n"
                        % (input_names[i], input_names[j], Si['S2'][i][j], Si['S2_conf'][i][j]))
        f.write("\n")

#----------------------------------------

def generate_sobol_seq(config_file_path, n, save=False):
    from SALib.sample import saltelli
    from nested.utils import read_from_yaml

    yaml_dict = read_from_yaml(config_file_path)
    bounds = None
    for name, bound in yaml_dict['bounds'].items():
        bounds = np.array(bound) if bounds is None else np.vstack((bounds, np.array(bound)))
    problem = {
        'num_vars' : len(yaml_dict['param_names']),
        'names' : yaml_dict['param_names'],
        'bounds' : bounds,
    }
    param_values = saltelli.sample(problem, n)

    if save:
        full_path = 'data/{}{}{}{}{}{}_sobol.hdf5'.format(*time.localtime())
        with h5py.File(full_path, 'a') as f:
            for i in range(param_values.shape[0]):
                f.create_group(str(i))
                f[str(i)]['x'] = param_values[i]
        print("Saved to {}.".format(full_path))
    return param_values

def get_idx(X_normed, sub):
    li = []
    for elem in sub:
        li.append(np.where(X_normed == elem)[0][0])
    return li

def plot_r_hm(pval_matrix, coef_matrix, input_names, output_names, p_baseline=.05):
    fig, ax = plt.subplots()
    mask = np.full_like(pval_matrix, True, dtype=bool)
    mask[pval_matrix < p_baseline] = False

    # edge case where the shape is (n, )
    if len(pval_matrix.shape) < 2:
        coef_matrix = coef_matrix.reshape(1, -1)
        mask = mask.reshape(1, -1)
    hm = sns.heatmap(coef_matrix, mask=mask, cmap='cool', fmt=".2f", linewidths=1, ax=ax, cbar=True, annot=True)
    hm.set_xticklabels(output_names)
    hm.set_yticklabels(input_names)
    plt.xticks(rotation=-90)
    plt.yticks(rotation=0)
    plt.show()
