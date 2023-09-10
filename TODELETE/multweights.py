import math
from collections import defaultdict

import sys
import numpy as np

from tqdm import tqdm, trange
import typing as t
import numpy.typing as npt

import KDTree2
import BallTree
import coreset as CORESET
import utils
from rounding import rand_round

import gurobipy as gp
from gurobipy import GRB

from lpsolve import solve_lp


def mult_weight_upd(gamma, N, k, features, colors, kis, epsilon):
    """
    uses the multiplicative weight update method to
    generate an integer solution for the LP
    :param gamma: the minimum distance to optimize for
    :param N: the number of elements in the dataset
    :param k: total number of points selected
    :param features: dataset's features
    :param colors: matching colors
    :param kis: the color->count mapping
    :param epsilon: allowed error value
    :return: a nx1 vector X of the solution or None if infeasible
    """
    assert(k > 0)

    scaled_eps = epsilon / (1.0 + (epsilon / 4.0))

    # for calculating error
    mu = k - 1

    h = np.full((N, 1), 1.0 / N, dtype=np.longdouble) # weights
    X = np.zeros((N, 1))         # Output

    T = ((8 * mu) / (math.pow(scaled_eps, 2))) * math.log(N, math.e) # iterations
    # for now, we can recreate the structure in advance
    struct = KDTree2.create(features)

    # NOTE: should this be <= or was it 1 indexed?
    for t in trange(math.ceil(T), desc='MWU Loop', disable=True):

        S = np.empty((0, features.shape[1]))  # points we select this round
        W = 0                                 # current weight sum

        # weights to every point
        w_sums = KDTree2.get_weight_ranges(struct, h, gamma / 2.0)
        print(w_sums)

        # compute minimums per color
        for color in kis.keys():
            # need this to reverse things
            color_sums_ind = (color == colors).nonzero()[0] # tuple for reasons

            # get minimum points as indices
            color_sums = w_sums[color_sums_ind]
            partition = np.argpartition(color_sums, kis[color] - 1)
            arg_mins = partition[:kis[color]]
            min_indecies = color_sums_ind[arg_mins]

            # add 1 to X[i]'s that are the minimum indices
            X[min_indecies] += 1
            # add points we've seen to S
            S = np.append(S, features[min_indecies], axis=0)
            # add additional weight to W
            W += np.sum(w_sums[min_indecies])

        if W >= 1:
            return None

        # get counts of points in each ball in M
        M = np.zeros_like(h)
        Z = BallTree.create(S)

        Cs = BallTree.get_counts_in_range(Z, features, gamma / 2.0)
        for i, c in enumerate(Cs):
            M[i] = (1.0 / mu) * ((-1 * c) + 1)

        # update H
        h = h * (np.ones_like(M) - ((scaled_eps / 4.0) * M))
        h /= np.sum(h)


        # check directly if X is a feasible solution
        if t % 50 == 0:
            X_weights = KDTree2.get_weight_ranges(struct, X / (1 + t), gamma / 2.0)
            if not np.any(X_weights > 1 + epsilon):
                break

    X = X / (t + 1)
    return X


def epsilon_falloff(features, colors, kis, gamma_upper, mwu_epsilon, falloff_epsilon):
    """
    starts at a high bound (given by the corset estimate) and repeatedly falls off by 1-epsilon
    :param features: the data set
    :param colors:   color labels for the data set
    :param kis:      map of colors to requested counts
    :param gamma_upper: the starting value for gamma
    :param mwu_epsilon: epsilon for the MWU method (static error)
    :param falloff_epsilon: epsilon for the falloff system (fraction to reduce by each cycle)
    :return:
    """

    N = len(features)
    k = sum(kis.values())

    timer = utils.Stopwatch("Falloff")

    gamma = gamma_upper

    X = mult_weight_upd(gamma, N, k, features, colors, kis, mwu_epsilon)
    while X is None:
        gamma = gamma * (1 - falloff_epsilon)
        X = mult_weight_upd(gamma, N, k, features, colors, kis, mwu_epsilon)

    timer.split("Randomized Rounding")

    # we need to flatten X since it expects an array rather than a 1D vector
    S = rand_round(gamma / 2.0, X.flatten(), features, colors, kis)

    _, total_time = timer.stop()

    # build up stats to return
    selected_count = len(S)
    solution = features[S]

    diversity = utils.compute_maxmin_diversity(solution)

    return selected_count, diversity, total_time


if __name__ == '__main__':
    # Testing field
    # File fields
    allFields = [
        "x",
        "y",
        "color",
    ]

    # fields we care about for parsing
    color_field = ['color']
    feature_fields = {'x', 'y'}

    # variables for running LP bin-search
    # keys are appended using underscores
    kis = {
        'blue': 2,
        'red': 1,
    }
    k = sum(kis.values())

    colors, features = utils.read_CSV("./datasets/mwutest/example.csv", allFields, color_field, '_', feature_fields)
    assert (len(colors) == len(features))

    # get the colors
    color_names = np.unique(colors)

    N = len(features)
    gamma = 5
    k = 3
    kis = {'blue': 2, 'red': 1}
    X = mult_weight_upd(5, N, k, features, colors, kis, 0.5)
    if X is None:
        print('None!')
    else:
        print(X)

    """
    # File fields
    allFields = [
        "age",
        "workclass",
        "fnlwgt",  # what on earth is this one?
        "education",
        "education-num",
        "marital-status",
        "occupation",
        "relationship",
        "race",
        "sex",
        "capital-gain",
        "capital-loss",
        "hours-per-week",
        "native-country",
        "yearly-income",
    ]

    # fields we care about for parsing
    color_field = ['race', 'sex']
    feature_fields = {'age', 'capital-gain', 'capital-loss', 'hours-per-week', 'fnlwgt', 'education-num'}

    colors, features = utils.read_CSV("./datasets/ads/adult.data", allFields, color_field, '_', feature_fields)
    assert (len(colors) == len(features))

    # get the colors
    color_names = np.unique(colors)

    # "normalize" features
    # Should happen before coreset construction
    means = features.mean(axis=0)
    devs = features.std(axis=0)
    features = (features - means) / devs


    # testing!
    results = []

    for k in range(10, 201, 5):
        # compute coreset
        coreset_size = 100 * k

        d = len(feature_fields)
        m = len(color_names)
        coreset = CORESET.Coreset_FMM(features, colors, k, m, d, coreset_size)
        core_features, core_colors = coreset.compute()

        gamma_upper = coreset.compute_gamma_upper_bound()

        # build KIs
        kis = utils.buildKisMap(colors, k, 0)

        # actually run the model
        selected, div, time = epsilon_falloff(
            features=core_features,
            colors=core_colors,
            kis=kis,
            gamma_upper=gamma_upper,
            mwu_epsilon=0.75,
            falloff_epsilon=0.1,
        )
        results.append((k, selected, div, time))

    print('\n\nFINAL RESULTS:')
    print('k\tselected\tdiversity\ttime')
    for k, selected, div, time in results:
        print(f'{k},\t{selected},\t{div},\t{time},')
    """