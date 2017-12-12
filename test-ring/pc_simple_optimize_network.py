#mpiexec -n 4 python pc_simple_optimize_network.py

from mpi4py import MPI
from neuron import h
import importlib
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moopgen import *

kwargs = {'cvode': False}


global_context = Context()
global_context.kwargs = kwargs
global_context.sleep = False
comm = MPI.COMM_WORLD
global_context.comm = comm


def setup_ranks():
    module_names = ['pc_simple_network_submodule']
    modules = []
    get_features_funcs = []
    for module_name in module_names:
        m = importlib.import_module(module_name)
        modules.append(m)
        #m.config_controller(**global_context.kwargs) #not really necessary
        get_features_funcs.append(getattr(m, 'get_feature'))
    global_context.modules = modules
    global_context.get_features_funcs = get_features_funcs

    #test whether config_controller becomes available on all -- yes it does!
    if comm.rank == 0:
        global_context.modules[0].controller_details()


def init_engine(**kwargs):
    setup_funcs = []
    for m in set(global_context.modules):
        config_func = getattr(m, 'config_engine')
        if not callable(config_func):
            raise Exception('parallel_optimize: init_engine: submodule: %s does not contain required callable: '
                            'config_engine' % str(m))
        else:
            config_func(global_context.comm, **kwargs)
        setup_funcs.append(getattr(m, 'setup_network'))
    global_context.setup_funcs = setup_funcs


def run_optimization():
    #should this happen only on one processor (rank = 0)? Parallel context exists only on the other module.

    #normally, we would be looping through multiple generations (each generated by param_gen)
    generation = [0, 1, 2, 3]
    features = get_all_features(generation)
    #next, get all objectives
    print 'final features'
    print features
    getattr(global_context.modules[0], 'end_optimization')()

def get_all_features(generation):
    pop_ids = range(len(generation))
    curr_generation = {pop_id: generation[pop_id] for pop_id in pop_ids}
    features_dict = {pop_id: {} for pop_id in pop_ids}

    x_vals = [0.01, 0.2, 0.5, 1.]

    global_context.get_features_funcs = global_context.get_features_funcs * 2 #so that we do 2 rounds of feature calculation
    global_context.setup_funcs = global_context.setup_funcs * 2

    for ind in xrange(len(global_context.get_features_funcs)):
        next_generation = {}
        indivs = [{'x': x_vals[pop_id], 'features': features_dict[pop_id]} for pop_id in curr_generation]
        feature_function = global_context.get_features_funcs[ind]
        #differs from old parallel_optimize script in that we are no longer mapping each indiv to a separate feature_function call
        print 'start round %i' %ind
        results = feature_function(indivs)
        for i, result in enumerate(results):
            if None in result['result_list']:
                print 'Individual: %i failed %s' %(result['pop_id'], str(str(feature_function)))
                features_dict[result['pop_id']] = None
            else:
            #Alternatively, consider processing features in the get_features function, since it has access to prior features
                next_generation[result['pop_id']] = generation[result['pop_id']]
                #do filter features processing here
                new_features = {key: value for result_dict in result['result_list'] for key, value in result_dict.iteritems()}
                features_dict[result['pop_id']].update(new_features)
        curr_generation = next_generation
        print 'features after round %i' %ind
        print features_dict
    features = [features_dict[pop_id] for pop_id in pop_ids]
    return features

if __name__ == '__main__':
    setup_ranks()
    init_engine()
    print global_context.modules[0].report_pc_id()
    run_optimization()
