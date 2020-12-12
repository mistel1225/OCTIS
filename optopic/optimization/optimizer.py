# Utils
import time
from pathlib import Path

# utils from numpy
import numpy as np

# utils from skopt and sklearn
from sklearn.gaussian_process.kernels import *
from skopt.space.space import *

from dataset.dataset import Dataset
# utils from other files of the framework
from optopic.models.model import save_model_output
from optopic.optimization.optimizer_tool import BestEvaluation
from optopic.optimization.optimizer_tool import plot_bayesian_optimization, plot_model_runs
from optopic.optimization.optimizer_tool import early_condition
from optopic.optimization.optimizer_tool import choose_optimizer


class Optimizer:
    """
    Class Optimizer to perform Bayesian Optimization on Topic Model
    """

    # Values of hyperparameters and metrics for each iteration
    _iterations = []  # counter for the BO iteration
    topk = 10  # if False the topk words will not be computed
    topic_word_matrix = True  # if False the matrix will not be computed
    topic_document_matrix = True  # if False the matrix will not be computed

    def __init__(self, model, dataset, metric, search_space, extra_metrics=[],
                 number_of_call=5, n_random_starts=0,
                 initial_point_generator="lhs",  # work only for version skopt 8.0!!!
                 optimization_type='Maximize', model_runs=5, surrogate_model="RF",
                 kernel=1.0 * Matern(length_scale=1.0, length_scale_bounds=(1e-1, 10.0), nu=1.5),
                 acq_func="LCB", random_state=False, x0=[], y0=[], 
                 save_models=False,save_step=1, save_name="result", save_path="results/", early_stop=False, early_step=5,
                 plot_best_seen=False, plot_model=False, plot_name="B0_plot", log_scale_plot=False):

        """
        Inizialization of the optimizer for the model

        Parameters
        ----------
        model          : model with hyperparameters to optimize
        dataset        : dateset for the topic model
        metric         : initialized metric to use for optimization
        search_space   : a dictionary of hyperparameters to optimize
                       (each parameter is defined as a skopt space)
                       with the name of the hyperparameter given as key
        extra_metrics: list of extra metric computed during BO
        number_of_call : number of calls to f
        n_random_starts: number of evaluations of f with random points before approximating it with minimizer  
        initial_point_generator: way to generate random points (lhs, random,sobol,halton,hammersly,grid,)
        optimization_type: maximization or minimization problem
        model_runs     : number of different evaluation of the function using the same hyper-parameter configuration
        surrogate_model: type of surrogate model (from sklearn)
        Kernel         : type of kernel (from sklearn)
        acq_func       : function to minimize over the minimizer prior (LCB,EI,PI)
        random_state   : set random state to something other than None for reproducible results.
        x0             : list of initial configurations to test
        y0             : list of values for x0
        save_models    : if True, all the model (number_of_call*model_runs) are saved
        save_step      : integer interval after which save the .pkl about BO file
        save_name      : name of the .csv and .pkl files
        save_path      : path where .pkl, plot and result will be saved.

        early_stop     : if True, an early stop policy is applied fro BO.
        early_step     : integer interval after which a current optimization run is stopped if it doesn't improve.
        plot_best_seen : if True the plot of the best seen for BO is showed
        plot_model     : if True the boxplot of all the model runs is done
        plot_name      : name of the plots (both for model runs or best_seen)
        log_scale_plot : if True the "y_axis" of the plot is set to log_scale

        """
        self.model = model
        self.dataset = dataset
        self.metric = metric
        self.search_space = search_space
        self.current_call = 0
        self.hyperparameters = list(sorted(self.search_space.keys()))
        self.extra_metrics = extra_metrics
        self.optimization_type = optimization_type
        self.dict_model_runs=dict()
        self.number_of_call = number_of_call
        self.n_random_starts = n_random_starts
        self.initial_point_generator = initial_point_generator
        self.model_runs = model_runs
        self.surrogate_model = surrogate_model
        self.kernel = kernel
        self.acq_func = acq_func
        self.random_state = random_state
        self.x0 = x0
        self.y0 = y0
        self.save_path = save_path
        self.save_step = save_step
        self.save_name = save_name                
        self.save_models = save_models
        self.early_stop = early_stop
        self.early_step = early_step
        self.plot_model = plot_model
        self.plot_best_seen = plot_best_seen
        self.plot_name = plot_name
        self.log_scale_plot = log_scale_plot

        # create the directory where the results are saved
        Path(self.save_path ).mkdir(parents=True, exist_ok=True)

        #inizialize the dictories about model_runs
        self.dict_model_runs[metric.__class__.__name__]=dict()
        for extra_metric in extra_metrics:
            self.dict_model_runs[extra_metric.__class__.__name__]=dict()

        # control about the correctness of Bo parameters
        if self.check_BO_parameters() == -1:
            print("ERROR: wrong inizialitation of BO parameters")
            return None

    def _objective_function(self, hyperparameters):
        """
        objective function to optimize

        Parameters
        ----------
        hyperparameters : dictionary of hyperparameters
                          (It's a list for real)
                          key: name of the parameter
                          value: skopt search space dimension

        Returns
        -------
        result : score of the metric to maximize
        """

        # Retrieve parameters labels
        params = {}
        for i in range(len(self.hyperparameters)):
            params[self.hyperparameters[i]] = hyperparameters[i]

        # Compute the score of the hyper-parameter configuration
        different_model_runs = []
        different_model_runs_extra_metrics=[[] for i in range(len(self.extra_metrics))]
        
        for i in range(self.model_runs):

            # Prepare model
            model_output = self.model.train_model(self.dataset, params,
                                                  self.topk,
                                                  self.topic_word_matrix,
                                                  self.topic_document_matrix)
            # Score of the model
            score = self.metric.score(model_output)
            different_model_runs.append(score)
            
            # Update of the extra metric values
            for j,extra_metric in enumerate(self.extra_metrics):
                different_model_runs_extra_metrics[j].append(extra_metric.score(model_output))

            # Save the model for each run
            if self.save_models:
                name = str(self.current_call) + "_" + str(i)
                save_model_path = self.model_path_models + name
                save_model_output(model_output, save_model_path)
        
        #update of the dictionaries
        self.dict_model_runs[self.metric.__class__.__name__]['iteration_'+str(self.current_call)]=different_model_runs
        
        for j,extra_metric in enumerate(self.extra_metrics):
            self.dict_model_runs[extra_metric.__class__.__name__]['iteration_'+str(self.current_call)]=different_model_runs_extra_metrics[j]
        
        # the output for BO is the median over different_model_runs
        result = np.median(different_model_runs)

        if self.optimization_type == 'Maximize':
            result = - result

        # Boxplot for matrix_model_runs
        if self.plot_model:       
            name_plot=self.save_path+self.plot_name + "_model_runs_" + self.metric.__class__.__name__
            plot_model_runs(self.dict_model_runs[self.metric.__class__.__name__],self.current_call,name_plot )
            
            # Boxplot of extrametrics (if any)
            for extra_metric in self.extra_metrics:
                name_plot=self.save_path+ self.plot_name + "_model_runs_" + self.metric.__class__.__name__
                plot_model_runs(self.dict_model_runs[extra_metric.__class__.__name__], self.current_call,name_plot )

        return result

    def optimize(self):
        """
        Optimize the hyperparameters of the model

        Returns
        
        ----------        
        an object containing all the information about BO:
            -func_vals    : function value for each optimization run 
            -y_best       : function value at the optimum
            -x_iters      : location of function evaluation for each optimization run
            -x_best       : location of the optimum   
            -models_runs  : dictionary about all the model runs 
            -extra_metrics: dictionary about all the model runs for the extra metrics

        """
        #### Choice of the optimizer
        opt=choose_optimizer(self);

        ####for loop to perform Bayesian Optimization     
        time_eval = []
        for i in range(self.number_of_call):

            print("Current call: ", self.current_call)            
            start_time = time.time()
            
            ### next point proposed by BO and evaluation of the objective function
            if i<len(self.x0):
                next_x=self.x0[i]
 
                if len(self.y0)==0:
                    self.dict_model_runs[self.metric.__class__.__name__]['iteration_'+str(i)]=self.y0[i]   
                    f_val=self._objective_function(next_x)
                else:
                    f_val=-self.y0[i] if self.optimization_type == 'Maximize' else self.y0[i]
     
            else:
                next_x = opt.ask()  
                f_val = self._objective_function(next_x)  

            ###update the opt using (next_x,f_val)
            res = opt.tell(next_x, f_val) 

            ### update the computational time for next_x (BO+Function evaluation)
            end_time = time.time()
            total_time_function = end_time - start_time 
            time_eval.append(total_time_function)

            ### Plot best seen
            if self.plot_best_seen:
                plot_bayesian_optimization(res.func_vals, self.save_path+self.plot_name + "_best_seen",
                                           self.log_scale_plot, conv_max=self.optimization_type == 'Maximize')

            ### Create an object related to the BO optimization
            results = BestEvaluation(self,resultsBO=res,times=time_eval)
            
            ### Save the object
            if i % self.save_step == 0:
                name_pkl =self.save_path+ self.save_name + ".json"
                results.save(name_pkl)

            ### Early stop condition
            if i>=len(self.x0) and self.early_stop and early_condition(res.func_vals, self.early_step, self.n_random_starts):
                print("Stop because of early stopping condition")
                break

            ###update current_call
            self.current_call=self.current_call+1

        return results

    def restart_optimize(self,
                         BestObject,
                         number_of_call,
                         model,
                         metric=None,
                         extra_metrics=[],
                         acq_func=None,
                         surrogate_model=None,
                         kernel=None,
                         optimization_type=None,
                         model_runs=None,
                         save_models=None,
                         save_step=None,
                         save_name=None,
                         save_path=None,                         
                         early_stop=None,
                         early_step=None,                         
                         plot_model=None,
                         plot_best_seen=None,
                         plot_name=None,
                         log_scale_plot=None,
                         search_space=None):    

        self.model=model
        
        ###re-inizialization of the parameters
        self.extra_metrics=extra_metrics 
        self.search_space=search_space if search_space else eval(BestObject["search_space"])
        self.acq_func =acq_func if acq_func else BestObject["acq_func"]
        self.surrogate_model =surrogate_model if surrogate_model else BestObject["surrogate_model"]       
        self.kernel =kernel if kernel else eval(BestObject["kernel"])            
        self.optimization_type = optimization_type if optimization_type else BestObject["optimization_type"]          
        self.model_runs = model_runs if model_runs else BestObject["model_runs"]
        self.save_models = save_models if save_models else BestObject["save_models"]     
        self.save_step = save_step if save_step else BestObject["save_step"]             
        self.save_models = save_models if save_models else BestObject["save_models"] 
        self.save_path = save_path if save_path else BestObject["save_path"]                       
        self.early_stop = early_stop if early_stop else BestObject["early_stop"]         
        self.early_step = early_step if early_step else BestObject["early_step"]   
        self.plot_model = plot_model if plot_model else BestObject["plot_model"]         
        self.plot_best_seen = plot_best_seen if plot_best_seen else BestObject["plot_best_seen"]         
        self.plot_name = plot_name if plot_name else BestObject["plot_name"]       
        self.log_scale_plot = log_scale_plot if log_scale_plot else BestObject["log_scale_plot"]    
        
        if metric is None:
            import evaluation_metrics.coherence_metrics as metrics
            metric_attributes=BestObject["metric_attributes"]
            self.metric=getattr(metrics,BestObject["metric_name"])(metric_attributes)
        else:
            self.metric=metric
           
        ###Load of the dataset
        dataset = Dataset()
        dataset.load(BestObject["dataset_path"])
        self.dataset=dataset
        
        #### Choice of the optimizer
        opt=choose_optimizer(self,restart=True);

        ####update of the model through x0,y0
        time_eval = BestObject["time"]

        #### update number_of_call for restarting
        number_of_previous_calls=len(time_eval)
        self.number_of_call=number_of_previous_calls+number_of_call
        self.current_call=number_of_previous_calls

        self.dict_model_runs=BestObject['dict_model_runs']
        
        for metric in self.extra_metrics:
            if metric.__class__.__name__ not in self.dict_model_runs.keys():
                self.dict_model_runs[metric.__class__.__name__]=dict()
                for i in range(number_of_previous_calls):
                    self.dict_model_runs[metric.__class__.__name__]["iteration_"+str(i)]=0
        
        for i in range(number_of_previous_calls):
            next_x=[BestObject["x_iters"][key][i] for key in self.hyperparameters]
            f_val=-BestObject["f_val"][i] if self.optimization_type == 'Maximize' else BestObject["f_val"][i]
            res = opt.tell(next_x, f_val)          
 
        ####for loop to perform Bayesian Optimization        
        for i in range(number_of_previous_calls,self.number_of_call):
            ### next point proposed by BO and evaluation of the objective function
            print("Current call: ", self.current_call)  
            ### next point proposed by BO and evaluation of the objective function
            start_time = time.time()
            next_x = opt.ask()  
            f_val = self._objective_function(next_x)

            #update the opt using (next_x,f_val)
            res = opt.tell(next_x, f_val)      
            
            ### update the computational time for next_x (BO+Function evaluation)
            end_time = time.time()
            total_time_function = end_time - start_time 
            time_eval.append(total_time_function)             
            #### Plot best seen
            if self.plot_best_seen:
                plot_bayesian_optimization(res.func_vals, self.save_path+self.plot_name + "_best_seen",
                                           self.log_scale_plot, conv_max=self.optimization_type == 'Maximize')

            ### Create an object related to the BO optimization
            results = BestEvaluation(self,resultsBO=res,times=time_eval)

            if i % self.save_step == 0:
                name_pkl =self.save_path+ self.save_name + ".json"
                results.save(name_pkl)

            # Early stop condition
            if i>=len(self.x0) and self.early_stop and early_condition(res.func_vals, self.early_step, self.n_random_starts):
                print("Stop because of early stopping condition")
                break

            ###update current_call
            self.current_call=self.current_call+1
            
        return results

    def check_BO_parameters(self):
        ###Controls about BO parameters
        if self.optimization_type not in ['Maximize', 'Minimize']:
            print("Error: optimization type must be Maximize or Minimize")
            return -1

        if self.surrogate_model not in ['RF', 'RS', 'GP', 'ET']:
            print("Error: surrogate model must be RF, ET, RS or GP")
            return -1

        if self.acq_func not in ['PI', 'EI', 'LCB']:
            print("Error: acquisition function must be PI, EI or LCB")
            return -1

        if self.number_of_call <= 0:
            print("Error: number_of_call can't be <= 0")
            return -1

        if self.number_of_call - len(self.x0) <= 0:
            print("Error: number_of_call is less then len(x0)")
            return None

        if not isinstance(self.model_runs, int):
            print("Error: model_run must be an integer")
            return -1

        if not isinstance(self.number_of_call, int):
            print("Error: number_of_call must be an integer")
            return -1

        if not isinstance(self.n_random_starts, int):
            print("Error: n_random_starts must be an integer")
            return -1

        if not isinstance(self.save_step, int):
            print("Error: save_step must be an integer")
            return -1

        if not isinstance(self.save_step, int):
            print("Error: save_step must be an integer")
            return -1

        if self.initial_point_generator not in ['lhs', 'sobol', 'halton', 'hammersly', 'grid', 'random']:
            print("Error: wrong initial_point_generator")
            return -1

        if self.plot_name.endswith(".png"):
            self.plot_name=self.plot_name[:-4]

        if self.save_name.endswith(".json"):
            self.save_name=self.save_name[:-4]

        if (self.save_path[-1] != '/'):
            self.save_path = self.save_path + '/'
          
        if self.save_models:
            self.model_path_models = self.save_path  + "models/"
            Path(self.model_path_models).mkdir(parents=True, exist_ok=True)

        return 0