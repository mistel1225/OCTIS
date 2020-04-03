from models.interface import Abstract_Model
from dataset.dataset import Dataset
import re
import gensim
import gensim.corpora as corpora


class LDA_Model(Abstract_Model):

    def set_default_hyperparameters(self):
        """
        Set hyperparameters default values for the model
        """
        self.hyperparameters = {
            'corpus': None,
            'num_topics': 100,
            'id2word': None,
            'distributed': False,
            'chunksize': 2000,
            'passes': 1,
            'update_every': 1,
            'alpha': 'symmetric',
            'eta': None,
            'decay': 0.5,
            'offset': 1.0,
            'eval_every': 10,
            'iterations': 50,
            'gamma_threshold': 0.001,
            'minimum_probability': 0.01,
            'random_state': None,
            'ns_conf': None,
            'minimum_phi_value': 0.01,
            'per_word_topics': False,
            'callbacks': None}

    def map_vocabulary(self):
        self.id2word = corpora.Dictionary(self.dataset.get_corpus())

    def build_model(self):
        """
        Adapt the corpus to the model
        """
        self.id_corpus = [self.id2word.doc2bow(
            document) for document in self.dataset.get_corpus()]
        self.builded = True
        self.trained = False

    def train_model(self):
        """
        Train the model and save all the data
        in trained_model
        """
        if self.builded:
            hyperparameters = self.hyperparameters
            self.trained_model = gensim.models.ldamodel.LdaModel(
                corpus=self.id_corpus,
                id2word=self.id2word,
                num_topics=hyperparameters["num_topics"],
                distributed=hyperparameters["distributed"],
                chunksize=hyperparameters["chunksize"],
                passes=hyperparameters["passes"],
                update_every=hyperparameters["update_every"],
                alpha=hyperparameters["alpha"],
                eta=hyperparameters["eta"],
                decay=hyperparameters["decay"],
                offset=hyperparameters["offset"],
                eval_every=hyperparameters["eval_every"],
                iterations=hyperparameters["iterations"],
                gamma_threshold=hyperparameters["gamma_threshold"],
                minimum_probability=hyperparameters["minimum_probability"],
                random_state=hyperparameters["random_state"],
                ns_conf=hyperparameters["ns_conf"],
                minimum_phi_value=hyperparameters["minimum_phi_value"],
                per_word_topics=hyperparameters["per_word_topics"],
                callbacks=hyperparameters["callbacks"])
            self.trained = True
            return True
        return False

    def make_topic_word_matrix(self):
        """
        Return False if the model is not trained,
        produce the document topic representation
        and return True otherwise
        """
        if self.trained:
            self.topic_word_matrix = self.trained_model.get_topics()
            return True
        return False

    def make_doc_topic_representation(self):
        """
        Return False if the model is not trained,
        produce the topic word matrix and return
        True otherwise
        """
        if self.trained:
            self.doc_topic_representation = self.trained_model.get_document_topics(
                self.id_corpus)
            return True
        return False
