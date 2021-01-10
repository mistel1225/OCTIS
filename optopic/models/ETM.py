from __future__ import print_function
from optopic.models.early_stopping.pytorchtools import EarlyStopping

import torch
import numpy as np
from optopic.models.ETM_model import data
from sklearn.feature_extraction.text import CountVectorizer
from torch import nn, optim
from optopic.models.ETM_model import etm
from optopic.models.model import Abstract_Model
import gensim
import pickle as pkl

class ETM(Abstract_Model):

    def __init__(self, num_topics=10, num_epochs=100, t_hidden_size=800, rho_size=300, embedding_size=300,
                 activation='relu', dropout=0.5, lr=0.005, optimizer='adam', batch_size=128, clip=0.0,
                 wdecay=1.2e-6, bow_norm=1, device='cpu', top_word=10, train_embeddings=True, embeddings_path=None):
        super(ETM, self).__init__()
        self.hyperparameters = dict()
        self.hyperparameters['num_topics'] = num_topics
        self.hyperparameters['num_epochs'] = num_epochs
        self.hyperparameters['t_hidden_size'] = t_hidden_size
        self.hyperparameters['rho_size'] = rho_size
        self.hyperparameters['embedding_size'] = embedding_size
        self.hyperparameters['activation'] = activation
        self.hyperparameters['dropout'] = dropout
        self.hyperparameters['lr'] = lr
        self.hyperparameters['optimizer'] = optimizer
        self.hyperparameters['batch_size'] = batch_size
        self.hyperparameters['clip'] = clip
        self.hyperparameters['wdecay'] = wdecay
        self.hyperparameters['bow_norm'] = bow_norm
        self.hyperparameters['train_embeddings'] = train_embeddings
        self.hyperparameters['embeddings_path'] = embeddings_path
        self.top_word = top_word
        self.early_stopping = None
        self.device = device
        self.test_tokens, self.test_counts = None, None
        self.valid_tokens, self.valid_counts = None, None
        self.train_tokens, self.train_counts, self.vocab = None, None, None
        self.use_partitions = False
        self.model = None
        self.optimizer = None
        self.embeddings = None

    def train_model(self, dataset, hyperparameters, top_words=10):
        self.set_model(dataset, hyperparameters)
        self.top_word = top_words
        self.early_stopping = EarlyStopping(patience=5, verbose=True)

        for epoch in range(0, self.hyperparameters['num_epochs']):
            continue_training = self._train_epoch(epoch)
            if not continue_training:
                break

        # load the last checkpoint with the best model
        # self.model.load_state_dict(torch.load('etm_checkpoint.pt'))

        if self.use_partitions:
            result = self.inference()
        else:
            result = self.get_info()

        return result

    def set_model(self, dataset, hyperparameters):
        if self.use_partitions:
            train_data, validation_data, testing_data = \
                dataset.get_partitioned_corpus(use_validation=True)

            data_corpus_train = [' '.join(i) for i in train_data]
            data_corpus_test = [' '.join(i) for i in testing_data]
            data_corpus_val = [' '.join(i) for i in validation_data]

            vocab = dataset.get_vocabulary()
            self.vocab = {i: w for i, w in enumerate(vocab)}
            vocab2id = {w: i for i, w in enumerate(vocab)}

            self.train_tokens, self.train_counts, self.test_tokens, self.test_counts, self.valid_tokens, \
            self.valid_counts = self.preprocess(vocab2id, data_corpus_train, data_corpus_test, data_corpus_val)

        else:
            data_corpus = [' '.join(i) for i in dataset.get_corpus()]
            vocab = dataset.get_vocabulary()
            self.vocab = {i: w for i, w in enumerate(vocab)}
            vocab2id = {w: i for i, w in enumerate(vocab)}

            self.train_tokens, self.train_counts = self.preprocess(vocab2id, data_corpus, None)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.set_default_hyperparameters(hyperparameters)
        self.load_embeddings()
        ## define model and optimizer
        self.model = etm.ETM(num_topics=self.hyperparameters['num_topics'], vocab_size=len(self.vocab.keys()),
                             t_hidden_size=self.hyperparameters['t_hidden_size'],
                             rho_size=self.hyperparameters['rho_size'],
                             emb_size=self.hyperparameters['embedding_size'],
                             theta_act=self.hyperparameters['activation'],
                             embeddings=self.embeddings,
                             train_embeddings=self.hyperparameters['train_embeddings'],
                             enc_drop=self.hyperparameters['dropout']).to(self.device)
        print('model: {}'.format(self.model))

        self.optimizer = self.set_optimizer()

    def set_optimizer(self):
        if self.hyperparameters['optimizer'] == 'adam':
            optimizer = optim.Adam(self.model.parameters(), lr=self.hyperparameters['lr'],
                                   weight_decay=self.hyperparameters['wdecay'])
        elif self.hyperparameters['optimizer'] == 'adagrad':
            optimizer = optim.Adagrad(self.model.parameters(), lr=self.hyperparameters['lr'],
                                      weight_decay=self.hyperparameters['wdecay'])
        elif self.hyperparameters['optimizer'] == 'adadelta':
            optimizer = optim.Adadelta(self.model.parameters(), lr=self.hyperparameters['lr'],
                                       weight_decay=self.hyperparameters['wdecay'])
        elif self.hyperparameters['optimizer'] == 'rmsprop':
            optimizer = optim.RMSprop(self.model.parameters(), lr=self.hyperparameters['lr'],
                                      weight_decay=self.hyperparameters['wdecay'])
        elif self.hyperparameters['optimizer'] == 'asgd':
            optimizer = optim.ASGD(self.model.parameters(), lr=self.hyperparameters['lr'],
                                   t0=0, lambd=0., weight_decay=self.hyperparameters['wdecay'])
        elif self.hyperparameters['optimizer'] == 'sgd':
            optimizer = optim.SGD(self.model.parameters(), lr=self.hyperparameters['lr'],
                                  weight_decay=self.hyperparameters['wdecay'])
        else:
            print('Defaulting to vanilla SGD')
            optimizer = optim.SGD(self.model.parameters(), lr=self.hyperparameters['lr'])

        return optimizer

    def _train_epoch(self, epoch):
        self.data_list = []
        self.model.train()
        acc_loss = 0
        acc_kl_theta_loss = 0
        cnt = 0
        indices = torch.arange(0, len(self.train_tokens))
        indices = torch.split(indices, self.hyperparameters['batch_size'])
        for idx, ind in enumerate(indices):
            self.optimizer.zero_grad()
            self.model.zero_grad()
            data_batch = data.get_batch(self.train_tokens, self.train_counts, ind, len(self.vocab.keys()),
                                        self.hyperparameters['embedding_size'], self.device)
            sums = data_batch.sum(1).unsqueeze(1)
            if self.hyperparameters['bow_norm']:
                normalized_data_batch = data_batch / sums
            else:
                normalized_data_batch = data_batch
            recon_loss, kld_theta = self.model(data_batch, normalized_data_batch)
            total_loss = recon_loss + kld_theta
            total_loss.backward()
            if self.hyperparameters["clip"] > 0:
                torch.nn.utils.clip_grad_norm_(self.model.parameters(),
                                               self.hyperparameters["clip"])
            self.optimizer.step()

            acc_loss += torch.sum(recon_loss).item()
            acc_kl_theta_loss += torch.sum(kld_theta).item()
            cnt += 1
            log_interval = 20
            if idx % log_interval == 0 and idx > 0:
                cur_loss = round(acc_loss / cnt, 2)
                cur_kl_theta = round(acc_kl_theta_loss / cnt, 2)
                cur_real_loss = round(cur_loss + cur_kl_theta, 2)

                print('Epoch: {} .. batch: {}/{} .. LR: {} .. KL_theta: {} .. Rec_loss: {}'
                      ' .. NELBO: {}'.format(epoch + 1, idx, len(indices),
                                             self.optimizer.param_groups[0]['lr'],
                                             cur_kl_theta, cur_loss, cur_real_loss))

            self.data_list.append(normalized_data_batch)

        cur_loss = round(acc_loss / cnt, 2)
        cur_kl_theta = round(acc_kl_theta_loss / cnt, 2)
        cur_real_loss = round(cur_loss + cur_kl_theta, 2)
        print('*' * 100)
        print('Epoch----->{} .. LR: {} .. KL_theta: {} .. Rec_loss: {} .. NELBO: {}'.format(
            epoch + 1, self.optimizer.param_groups[0]['lr'], cur_kl_theta, cur_loss,
            cur_real_loss))
        print('*' * 100)

        # VALIDATION ###
        if self.valid_tokens is None:
            return True
        else:
            model = self.model.to(self.device)
            model.eval()
            with torch.no_grad():
                val_acc_loss = 0
                val_acc_kl_theta_loss = 0
                val_cnt = 0
                indices = torch.arange(0, len(self.valid_tokens))
                indices = torch.split(indices, self.hyperparameters['batch_size'])
                for idx, ind in enumerate(indices):
                    self.optimizer.zero_grad()
                    self.model.zero_grad()
                    val_data_batch = data.get_batch(self.valid_tokens, self.valid_counts,
                                                    ind, len(self.vocab.keys()),
                                                    self.hyperparameters['embedding_size'], self.device)
                    sums = val_data_batch.sum(1).unsqueeze(1)
                    if self.hyperparameters['bow_norm']:
                        val_normalized_data_batch = val_data_batch / sums
                    else:
                        val_normalized_data_batch = val_data_batch

                    val_recon_loss, val_kld_theta = self.model(val_data_batch,
                                                               val_normalized_data_batch)

                    val_acc_loss += torch.sum(val_recon_loss).item()
                    val_acc_kl_theta_loss += torch.sum(val_kld_theta).item()
                    val_cnt += 1
                    val_total_loss = val_recon_loss + val_kld_theta

                val_cur_loss = round(val_acc_loss / cnt, 2)
                val_cur_kl_theta = round(val_acc_kl_theta_loss / cnt, 2)
                val_cur_real_loss = round(val_cur_loss + val_cur_kl_theta, 2)
                print('*' * 100)
                print('VALIDATION .. LR: {} .. KL_theta: {} .. Rec_loss: {} .. NELBO: {}'.format(
                    self.optimizer.param_groups[0]['lr'], val_cur_kl_theta, val_cur_loss,
                    val_cur_real_loss))
                print('*' * 100)
                if np.isnan(val_cur_real_loss):
                    return False
                else:
                    self.early_stopping(val_total_loss, model)

                    if self.early_stopping.early_stop:
                        print("Early stopping")
                        return False
                    else:
                        return True

    def get_info(self):
        topic_w = []
        self.model.eval()
        info = {}
        with torch.no_grad():
            theta, _ = self.model.get_theta(torch.cat(self.data_list))
            gammas = self.model.get_beta().cpu().numpy()
            for k in range(self.hyperparameters['num_topics']):
                if np.isnan(gammas[k]).any():
                    # to deal with nan matrices
                    topic_w = None
                    break
                else:
                    top_words = list(gammas[k].argsort()[-self.top_word:][::-1])
                topic_words = [self.vocab[a] for a in top_words]
                topic_w.append(topic_words)

        info['topic-word-matrix'] = gammas
        info['topic-document-matrix'] = theta.cpu().detach().numpy().T
        info['topics'] = topic_w
        print(info['topics'])
        return info

    def inference(self):
        assert isinstance(self.use_partitions, bool) and self.use_partitions
        topic_d = []
        self.model.eval()
        indices = torch.arange(0, len(self.test_tokens))
        indices = torch.split(indices, self.hyperparameters['batch_size'])

        for idx, ind in enumerate(indices):
            data_batch = data.get_batch(self.test_tokens, self.test_counts,
                                        ind, len(self.vocab.keys()),
                                        self.hyperparameters['embedding_size'], self.device)
            sums = data_batch.sum(1).unsqueeze(1)
            if self.hyperparameters['bow_norm']:
                normalized_data_batch = data_batch / sums
            else:
                normalized_data_batch = data_batch
            theta, _ = self.model.get_theta(normalized_data_batch)
            topic_d.append(theta.cpu().detach().numpy())

        info = self.get_info()
        emp_array = np.empty((0, self.hyperparameters['num_topics']))
        topic_doc = np.asarray(topic_d)
        length = topic_doc.shape[0]
        # batch concatenation
        for i in range(length):
            emp_array = np.concatenate([emp_array, topic_doc[i]])
        info['test-topic-document-matrix'] = emp_array.T

        return info

    def set_default_hyperparameters(self, hyperparameters):
        for k in hyperparameters.keys():
            if k in self.hyperparameters.keys():
                self.hyperparameters[k] = hyperparameters.get(k, self.hyperparameters[k])

    def partitioning(self, use_partitions=False):
        self.use_partitions = use_partitions

    @staticmethod
    def preprocess(vocab2id, train_corpus, test_corpus=None, validation_corpus=None):
        # def split_bow(bow_in, n_docs):
        #    indices = np.asarray([np.asarray([w for w in bow_in[doc, :].indices]) for doc in range(n_docs)])
        #    counts = np.asarray([np.asarray([c for c in bow_in[doc, :].data]) for doc in range(n_docs)])
        #    return np.expand_dims(indices, axis=0), np.expand_dims(counts, axis=0)

        def split_bow(bow_in, n_docs):
            indices = [[w for w in bow_in[doc, :].indices] for doc in range(n_docs)]
            counts = [[c for c in bow_in[doc, :].data] for doc in range(n_docs)]
            return indices, counts

        vec = CountVectorizer(
            vocabulary=vocab2id, token_pattern=r'(?u)\b\w+\b')

        dataset = train_corpus
        if test_corpus is not None:
            dataset.extend(test_corpus)
        if validation_corpus is not None:
            dataset.extend(validation_corpus)

        vec.fit(dataset)
        idx2token = {v: k for (k, v) in vec.vocabulary_.items()}

        x_train = vec.transform(train_corpus)
        x_train_tokens, x_train_count = split_bow(x_train, x_train.shape[0])

        if test_corpus is not None:
            x_test = vec.transform(test_corpus)
            x_test_tokens, x_test_count = split_bow(x_test, x_test.shape[0])

            if validation_corpus is not None:
                x_validation = vec.transform(validation_corpus)
                x_val_tokens, x_val_count = split_bow(x_validation, x_validation.shape[0])

                return x_train_tokens, x_train_count, x_test_tokens, x_test_count, x_val_tokens, x_val_count
            else:
                return x_train_tokens, x_train_count, x_test_tokens, x_test_count
        else:
            if validation_corpus is not None:
                x_validation = vec.transform(validation_corpus)
                x_val_tokens, x_val_count = split_bow(x_validation, x_validation.shape[0])
                return x_train_tokens, x_train_count, x_val_tokens, x_val_count
            else:
                return x_train_tokens, x_train_count

    def load_embeddings(self):
        if not self.hyperparameters['train_embeddings']:
            vectors = {}
            embs = pkl.load(open(self.hyperparameters['embeddings_path'], 'rb'))
            for l in embs:
                line = l.split()
                word = line[0]
                if word in self.vocab.values():
                    vect = np.array(line[1:]).astype(np.float)
                    vectors[word] = vect
            embeddings = np.zeros((len(self.vocab.keys()), self.hyperparameters['embedding_size']))
            words_found = 0
            for i, word in enumerate(self.vocab.values()):
                try:
                    embeddings[i] = vectors[word]
                    words_found += 1
                except KeyError:
                    embeddings[i] = np.random.normal(scale=0.6, size=(self.hyperparameters['embedding_size'],))
            self.embeddings = torch.from_numpy(embeddings).to(self.device)

    def filter_pretrained_embeddings(self, pretrained_embeddings_path, save_embedding_path, vocab_path, binary=True):
        """
        Filter the embeddings from a set of word2vec-format pretrained embeddings based on the vocabulary
        This should allow you to avoid to load the whole embedding space every time you do Bayesian Optimization
        but just the embeddings that are in the vocabulary.
        :param pretrained_embeddings_path:
        :return:
        """
        vocab = []
        with open(vocab_path, 'r') as fr:
            for line in fr.readlines():
                vocab.append(line.strip().split(" ")[0])

        w2v_model = gensim.models.KeyedVectors.load_word2vec_format(pretrained_embeddings_path, binary=binary)
        embeddings = []
        for word in vocab:
            if word in w2v_model.vocab:
                line = word
                for w in w2v_model[word].tolist():
                    line = line + " " + str(w)
                embeddings.append(line)
        pkl.dump(embeddings, open(save_embedding_path, 'wb'))
