
import math
import numpy as np
from tqdm import trange
import sys

from datastructures.WeightedTree import WeightedTree
import datastructures.BallTree as BallTree
from algorithms.rounding import rand_round
import algorithms.utils as algsU
import datasets.utils as datsU
import algorithms.coreset as CORESET

def mult_weight_upd(gamma, N, k, features, colors, c_tree : WeightedTree, kis, epsilon, percent_theoretical_limit=1.0):
    """
    uses the multiplicative weight update method to
    generate an integer solution for the LP
    :param gamma: the minimum distance to optimize for
    :param N: the number of elements in the dataset
    :param k: total number of points selected
    :param features: dataset's features
    :param colors: matching colors
    :param c_tree: The ParGeo C++ tree on the features (passed in so we can re-use it across problem instances)
    :param kis: the color->count mapping
    :param epsilon: allowed error value
    :param percent_theoretical_limit: Percentage of the theoretical maximum number of iterations to run (default 1.0)
    :return: a nx1 vector X of the solution or None if infeasible
    :return: the "removed time" for encoding and decoding
    """

    """
        Things to try
            - softmax
            - sampling based on weights
            - counting approximation approach (ask professor for writeup)
            - random ideas?
    """

    gen = np.random.default_rng()
    def getNextSolutionCheckWait():
        return gen.integers(9, 29)

    nextSolutionCheckWait = getNextSolutionCheckWait()

    assert(k > 0)

    # time spent translating queries
    translation_time = 0

    scaled_eps = epsilon / (1.0 + (epsilon / 4.0))

    # for calculating error
    mu = k - 1

    h = np.full((N, 1), 1.0 / N, dtype=np.double) # weights
    X = np.zeros((N, 1))         # Output

    T = ((8 * mu) / (math.pow(scaled_eps, 2))) * math.log(N, math.e) # iterations
    # scale by the amount of the theoretical limit requested
    T *= percent_theoretical_limit

    # for now, we can recreate the structure in advance
    # dim = features.shape[1]
    # struct = WeightedTree(dim)
    # struct.construct_tree(features)

    for t in trange(math.ceil(T), desc='MWU Loop', disable=False):
        S = np.empty((0, features.shape[1]))  # points we select this round
        W = 0                                 # current weight sum

        # weights to every point (time is ignored for now)
        timer = algsU.Stopwatch("Query")
        inner_time, w_sums = c_tree.run_query(gamma / 2.0, h)
        _, outer_time = timer.stop()
        translation_time += (outer_time - inner_time)

        # compute minimums per color
        # TODO pre-compute as much of this loop as possible
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
            # struct.delete_tree()
            return None, translation_time

        # get counts of points in each ball in M
        M = np.zeros_like(h)
        Z = BallTree.create(S)

        Cs = BallTree.get_counts_in_range(Z, features, gamma / 2.0)
        for i, c in enumerate(Cs):
            M[i] = (1.0 / mu) * ((-1 * c) + 1)

        # update H
        h = h * (np.ones_like(M) - ((scaled_eps / 4.0) * M))

        h /= np.sum(h)

        # TODO: check rate of change of X and h (euclidean distance) or l-inf

        # check directly if X is a feasible solution
        if t > 100 and nextSolutionCheckWait == 0:
            # reset the wait to a new time
            nextSolutionCheckWait = getNextSolutionCheckWait()

            timer = algsU.Stopwatch("Query")

            # TODO: new query function => boolean for "valid solution"
            _, X_weights = c_tree.run_query(gamma / 2.0, (X / (t + 1)))
            _, outer_time = timer.stop()
            translation_time += (outer_time - inner_time)

            if not np.any(X_weights > 1 + epsilon):
                break
        else:
            nextSolutionCheckWait -= 1

    X = X / (t + 1)
    return X, translation_time


def epsilon_falloff(features, colors, kis, gamma_upper, mwu_epsilon, falloff_epsilon, return_unadjusted, percent_theoretical_limit=1.0):
    """
    starts at a high bound (given by the corset estimate) and repeatedly falls off by 1-epsilon
    :param features: the data set
    :param colors:   color labels for the data set
    :param kis:      map of colors to requested counts
    :param gamma_upper: the starting value for gamma
    :param mwu_epsilon: epsilon for the MWU method (static error)
    :param falloff_epsilon: epsilon for the falloff system (fraction to reduce by each cycle)
    :param return_unadjusted: someone made this option worthless
    :param percent_theoretical_limit: Percentage of the theoretical maximum number of iterations to run (default 1.0)
    :return:
    """

    # translation time to subtract from the total time
    translation_time = 0

    N = len(features)
    k = sum(kis.values())

    timer = algsU.Stopwatch("Falloff")

    gamma = gamma_upper

    # a ParGeo tree for good measure
    dim = features.shape[1]
    pargeo_tree = WeightedTree(dim)
    pargeo_tree.construct_tree(features)

    X, cur_trans_time = mult_weight_upd(gamma, N, k, features, colors, pargeo_tree, kis, mwu_epsilon, percent_theoretical_limit)
    translation_time += cur_trans_time

    while X is None:
        gamma = gamma * (1 - falloff_epsilon)
        X, cur_trans_time = mult_weight_upd(gamma, N, k, features, colors, pargeo_tree, kis, mwu_epsilon, percent_theoretical_limit)
        translation_time += cur_trans_time

    # "clean up" our tree
    pargeo_tree.delete_tree()

    timer.split("Randomized Rounding")

    # we need to flatten X since it expects an array rather than a 1D vector
    S = rand_round(gamma / 2.0, X.flatten(), features, colors, kis)

    _, total_time = timer.stop()

    adjusted_time = total_time - translation_time

    # build up stats to return
    selected_count = len(S)
    solution = features[S]

    diversity = algsU.compute_maxmin_diversity(solution)

    # TODO: Return solution set instead of size of solution
    if return_unadjusted:
        return S, diversity, adjusted_time, total_time
    else:
        return S, diversity, adjusted_time


if __name__ == '__main__':
    """ 
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

    colors, features = datsU.read_CSV("./datasets/ads/adult.data", allFields, color_field, '_', feature_fields)
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

    for k in range(10, 21, 10):
        # build KIs
        kis = algsU.buildKisMap(colors, k, 0)

        adj_k = sum(kis.values())

        # compute coreset
        coreset_size = 10 * k

        d = len(feature_fields)
        m = len(color_names)
        coreset = CORESET.Coreset_FMM(features, colors, adj_k, m, d, coreset_size)
        core_features, core_colors = coreset.compute()

        gamma_upper = coreset.compute_gamma_upper_bound()

        # actually run the model
        print(f'running mwu for {k}')
        selected, div, adj_time, time = epsilon_falloff(
            features=core_features,
            colors=core_colors,
            kis=kis,
            gamma_upper=gamma_upper,
            mwu_epsilon=0.75,
            falloff_epsilon=0.1,
            return_unadjusted=True,
            percent_theoretical_limit=0.4,
        )
        print(f'Finished! (time={time}) (adjusted={adj_time})')
        results.append((adj_k, selected, div, adj_time))

    print('\n\nFINAL RESULTS:')
    print('k\tselected\tdiversity\ttime')
    for k, selected, div, time in results:
        print(f'{k},\t{len(selected)},\t{div},\t{time},')

    """

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
