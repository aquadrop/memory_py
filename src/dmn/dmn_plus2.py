from __future__ import print_function
from __future__ import division

import sys
import time
import pickle

import numpy as np
from copy import deepcopy

import tensorflow as tf
from dmn.attention_gru_cell import AttentionGRUCell

from tensorflow.contrib.cudnn_rnn.python.ops import cudnn_rnn_ops


class Config(object):
    """Holds model hyperparams and data information."""

    batch_size = 64
    embed_size = 300
    hidden_size = 128

    max_epochs = 345
    early_stopping = 20

    dropout = 0.9
    lr = 0.001
    l2 = 0.001

    cap_grads = True
    max_grad_val = 10
    noisy_grads = True

    word2vec_init = True
    embedding_init = np.sqrt(3)

    # set to zero with strong supervision to only train gates
    strong_supervision = False
    beta = 1

    # NOTE not currently used hence non-sensical anneal_threshold
    anneal_threshold = 1000
    anneal_by = 1.5

    num_hops = 3
    num_attention_features = 4

    max_allowed_inputs = 130
    total_num = 20000

    floatX = np.float32

    multi_label = True
    top_k = 5
    max_memory_size = 20
    fix_vocab = True

    train_mode = True

    # paths
    DATA_DIR = '/home/ecovacs/work/memory_py/data/memn2n/train/tree/origin/'
    CANDID_PATH = '/home/ecovacs/work/memory_py/data/memn2n/train/tree/origin/candidates.txt'

    MULTI_DATA_DIR = '/home/ecovacs/work/memory_py/data/memn2n/train/multi_tree'
    MULTI_CANDID_PATH = '/home/ecovacs/work/memory_py/data/memn2n/train/multi_tree/candidates.txt'

    data_dir = MULTI_DATA_DIR if multi_label else DATA_DIR
    candid_path = MULTI_CANDID_PATH if multi_label else CANDID_PATH

    metadata_path = '/home/ecovacs/work/memory_py/data/memn2n/dmn_processed/metadata.pkl'
    data_path = '/home/ecovacs/work/memory_py/data/memn2n/dmn_processed/data.pkl'
    ckpt_path = '/home/ecovacs/work/memory_py/model/dmn/ckpt/'

    multi_metadata_path = '/home/ecovacs/work/memory_py/data/memn2n/dmn_processed/multi_metadata.pkl'
    multi_data_path = '/home/ecovacs/work/memory_py/data/memn2n/dmn_processed/multi_data.pkl'
    multi_ckpt_path = '/home/ecovacs/work/memory_py/model/dmn/multi_ckpt/'

    metadata_path = multi_metadata_path if multi_label else metadata_path
    data_path = multi_data_path if multi_label else data_path
    ckpt_path = multi_ckpt_path if multi_label else ckpt_path


def _add_gradient_noise(t, stddev=1e-3, name=None):
    """Adds gradient noise as described in http://arxiv.org/abs/1511.06807
    The input Tensor `t` should be a gradient.
    The output will be `t` + gaussian noise.
    0.001 was said to be a good fixed value for memory networks."""
    # with tf.op_scope([t, stddev], name, "add_gradient_noise") as name:
    with tf.name_scope(name, "add_gradient_noise", [t, stddev]) as name:
        t = tf.convert_to_tensor(t, name="t")
        gn = tf.random_normal(tf.shape(t), stddev=stddev)
        return tf.add(t, gn, name=name)


# from https://github.com/domluna/memn2n


def _position_encoding(sentence_size, embedding_size):
    """Position encoding described in section 4.1 in "End to End Memory Networks" (http://arxiv.org/pdf/1503.08895v5.pdf)"""
    encoding = np.ones((embedding_size, sentence_size), dtype=np.float32)
    ls = sentence_size + 1
    le = embedding_size + 1
    for i in range(1, le):
        for j in range(1, ls):
            encoding[i - 1, j - 1] = (i - (le - 1) / 2) * (j - (ls - 1) / 2)
    encoding = 1 + 4 * encoding / embedding_size / sentence_size
    return np.transpose(encoding)


class DMN_PLUS(object):
    def load_data(self, debug=False):
        """Loads data from metadata"""
        with open(self.config.metadata_path, 'rb') as f:
            metadata = pickle.load(f)

        self.word_embedding = np.asarray(metadata['word_embedding'])
        # print(type(self.word_embedding))
        self.max_q_len = metadata['max_q_len']
        self.max_input_len = metadata['max_input_len']
        self.max_sen_len = metadata['max_sen_len']
        self.num_supporting_facts = metadata['num_supporting_facts']
        self.vocab_size = metadata['vocab_size']
        self.candidate_size = metadata['candidate_size']
        self.candid2idx = metadata['candid2idx']
        self.idx2candid = metadata['idx2candid']
        self.w2idx = metadata['w2idx']
        self.idx2w = metadata['idx2w']

        if self.config.train_mode:
            print('Load metadata (training mode)')

            with open(self.config.data_path, 'rb') as f:
                data = pickle.load(f)

            self.train = data['train']
            self.valid = data['valid']
        else:
            print('Load metadata (infer mode)')

        self.encoding = _position_encoding(
            self.max_sen_len, self.config.embed_size)

        # print(self.idx2w)
        # print(self.idx2candid)

    def add_placeholders(self):
        """add data placeholder to graph"""
        self.question_placeholder = tf.placeholder(
            tf.int32, shape=(None, self.max_q_len), name='question')
        self.question_len_placeholder = tf.placeholder(
            tf.int32, shape=(None,), name='question_len')

        self.input_placeholder = tf.placeholder(tf.int32, shape=(
            None, self.max_input_len, self.max_sen_len), name='input')
        self.input_len_placeholder = tf.placeholder(
            tf.int32, shape=(None,), name='input_len')

        if self.config.multi_label:
            self.answer_placeholder = tf.placeholder(
                tf.float32, shape=(None, self.candidate_size), name='answer')
        else:
            self.answer_placeholder = tf.placeholder(
                tf.int32, shape=(None,), name='answer')

        # self.answer_len_placeholder = tf.placeholder(
        #     tf.int32, shape=(self.config.batch_size,))

        self.rel_label_placeholder = tf.placeholder(tf.int32, shape=(
            None, self.num_supporting_facts), name='rel_label')

        self.dropout_placeholder = tf.placeholder(tf.float32, name='dropout')

    def get_predictions(self, output):
        if self.config.multi_label:
            # multi targets
            predict_by_value = tf.nn.top_k(
                output, k=self.config.top_k, name="predict_op")
            predict_proba_op = tf.nn.softmax(output, name="predict_proba_op")
            pred = tf.nn.top_k(
                predict_proba_op, k=self.config.top_k, name="top_predict_proba_op")
        else:
            preds = tf.nn.softmax(output)
            pred = tf.argmax(preds, 1)

        return pred

    def add_loss_op(self, output):
        """Calculate loss"""
        # optional strong supervision of attention with supporting facts
        gate_loss = 0
        if self.config.strong_supervision:
            for i, att in enumerate(self.attentions):
                labels = tf.gather(tf.transpose(self.rel_label_placeholder), 0)
                gate_loss += tf.reduce_sum(
                    tf.nn.sparse_softmax_cross_entropy_with_logits(logits=att, labels=labels))

        # loss = self.config.beta * tf.reduce_sum(tf.nn.sparse_softmax_cross_entropy_with_logits(
        #     logits=output, labels=self.answer_placeholder)) + gate_loss
        if self.config.multi_label:
            # multi targets
            loss = self.config.beta * tf.reduce_sum(tf.nn.sigmoid_cross_entropy_with_logits(
                logits=output, labels=self.answer_placeholder)) + gate_loss
        else:
            loss = self.config.beta * tf.reduce_sum(tf.nn.sparse_softmax_cross_entropy_with_logits(
                logits=output, labels=self.answer_placeholder)) + gate_loss

        # stackoverflow :https://stackoverflow.com/questions/33712178/tensorflow-nan-bug
        # cross_entropy = - tf.reduce_sum(self.answer_placeholder *
        #                                 tf.log(tf.clip_by_value(output, 1e-10, 1.0)))
        # loss = self.config.beta * tf.reduce_sum(cross_entropy) + gate_loss

        # add l2 regularization for all variables except biases
        for v in tf.trainable_variables():
            if not 'bias' in v.name.lower():
                loss += self.config.l2 * tf.nn.l2_loss(v)

        tf.summary.scalar('loss', loss)

        return loss

    def add_training_op(self, loss):
        """Calculate and apply gradients"""
        opt = tf.train.AdamOptimizer(learning_rate=self.config.lr)
        gvs = opt.compute_gradients(loss)

        # optionally cap and noise gradients to regularize
        if self.config.cap_grads:
            gvs = [(tf.clip_by_norm(grad, self.config.max_grad_val), var)
                   for grad, var in gvs]
        if self.config.noisy_grads:
            gvs = [(_add_gradient_noise(grad), var) for grad, var in gvs]

        train_op = opt.apply_gradients(gvs)
        return train_op

    def get_question_representation(self, embeddings):
        """Get question vectors via embedding and GRU"""
        questions = tf.nn.embedding_lookup(
            embeddings, self.question_placeholder)

        gru_cell = tf.contrib.rnn.GRUCell(self.config.hidden_size)
        _, q_vec = tf.nn.dynamic_rnn(gru_cell,
                                     questions,
                                     dtype=np.float32,
                                     sequence_length=self.question_len_placeholder
                                     )

        return q_vec

    def get_input_representation(self, embeddings):
        """Get fact (sentence) vectors via embedding, positional encoding and bi-directional GRU"""
        # get word vectors from embedding
        inputs = tf.nn.embedding_lookup(embeddings, self.input_placeholder)

        # use encoding to get sentence representation
        inputs = tf.reduce_sum(inputs * self.encoding, 2)

        forward_gru_cell = tf.contrib.rnn.GRUCell(self.config.hidden_size)
        backward_gru_cell = tf.contrib.rnn.GRUCell(self.config.hidden_size)
        outputs, _ = tf.nn.bidirectional_dynamic_rnn(
            forward_gru_cell,
            backward_gru_cell,
            inputs,
            dtype=np.float32,
            sequence_length=self.input_len_placeholder
        )

        # f<-> = f-> + f<-
        fact_vecs = tf.reduce_sum(tf.stack(outputs), axis=0)

        # apply dropout
        fact_vecs = tf.nn.dropout(fact_vecs, self.dropout_placeholder)

        return fact_vecs

    def get_attention(self, q_vec, prev_memory, fact_vec, reuse):
        """Use question vector and previous memory to create scalar attention for current fact"""
        with tf.variable_scope("attention", reuse=reuse):
            features = [fact_vec * q_vec,
                        fact_vec * prev_memory,
                        tf.abs(fact_vec - q_vec),
                        tf.abs(fact_vec - prev_memory)]

            feature_vec = tf.concat(features, 1)

            attention = tf.contrib.layers.fully_connected(feature_vec,
                                                          self.config.embed_size,
                                                          activation_fn=tf.nn.tanh,
                                                          reuse=reuse, scope="fc1")

            attention = tf.contrib.layers.fully_connected(attention,
                                                          1,
                                                          activation_fn=None,
                                                          reuse=reuse, scope="fc2")

        return attention

    def generate_episode(self, memory, q_vec, fact_vecs, hop_index):
        """Generate episode by applying attention to current fact vectors through a modified GRU"""

        attentions = [tf.squeeze(
            self.get_attention(q_vec, memory, fv, bool(hop_index) or bool(i)), axis=1)
            for i, fv in enumerate(tf.unstack(fact_vecs, axis=1))]

        attentions = tf.transpose(tf.stack(attentions))
        self.attentions.append(attentions)
        attentions = tf.nn.softmax(attentions)
        attentions = tf.expand_dims(attentions, axis=-1)

        reuse = True if hop_index > 0 else False

        # concatenate fact vectors and attentions for input into attGRU
        gru_inputs = tf.concat([fact_vecs, attentions], 2)

        with tf.variable_scope('attention_gru', reuse=reuse):
            _, episode = tf.nn.dynamic_rnn(AttentionGRUCell(self.config.hidden_size),
                                           gru_inputs,
                                           dtype=np.float32,
                                           sequence_length=self.input_len_placeholder
                                           )

        return episode

    def add_answer_module(self, rnn_output, q_vec):
        """Linear softmax answer module"""

        rnn_output = tf.nn.dropout(rnn_output, self.dropout_placeholder)

        output = tf.layers.dense(tf.concat([rnn_output, q_vec], 1),
                                 self.candidate_size,
                                 activation=None)

        return output

    def inference(self):
        """Performs inference on the DMN model"""

        # set up embedding
        embeddings = tf.Variable(
            self.word_embedding.astype(np.float32), name="Embedding")

        # input fusion module
        with tf.variable_scope("question", initializer=tf.contrib.layers.xavier_initializer()):
            print('==> get question representation')
            q_vec = self.get_question_representation(embeddings)

        with tf.variable_scope("input", initializer=tf.contrib.layers.xavier_initializer()):
            print('==> get input representation')
            fact_vecs = self.get_input_representation(embeddings)

        # keep track of attentions for possible strong supervision
        self.attentions = []

        # memory module
        with tf.variable_scope("memory", initializer=tf.contrib.layers.xavier_initializer()):
            print('==> build episodic memory')

            # generate n_hops episodes
            prev_memory = q_vec

            for i in range(self.config.num_hops):
                # get a new episode
                print('==> generating episode', i)
                episode = self.generate_episode(
                    prev_memory, q_vec, fact_vecs, i)

                # untied weights for memory update
                with tf.variable_scope("hop_%d" % i):
                    prev_memory = tf.layers.dense(tf.concat([prev_memory, episode, q_vec], 1),
                                                  self.config.hidden_size,
                                                  activation=tf.nn.relu)

            output = prev_memory

        # pass memory module output through linear answer module
        with tf.variable_scope("answer", initializer=tf.contrib.layers.xavier_initializer()):
            output = self.add_answer_module(output, q_vec)

        return output

    def run_epoch(self, session, data, num_epoch=0, train_writer=None, train_op=None, verbose=2, train=False):
        config = self.config
        dp = config.dropout
        if train_op is None:
            train_op = tf.no_op()
            dp = 1
        total_steps = len(data[0]) // config.batch_size

        total_loss = []
        accuracy = 0
        error = []

        # shuffle data
        p = np.random.permutation(len(data[0]))
        qp, ip, ql, il, im, a, r = data
        qp, ip, ql, il, im, a, r = qp[p], ip[p], ql[p], il[p], im[p], a[p], r[p]

        for step in range(total_steps):
            index = range(step * config.batch_size,
                          (step + 1) * config.batch_size)
            feed = {self.question_placeholder: qp[index],
                    self.input_placeholder: ip[index],
                    self.question_len_placeholder: ql[index],
                    self.input_len_placeholder: il[index],
                    self.answer_placeholder: a[index],
                    self.rel_label_placeholder: r[index],
                    self.dropout_placeholder: dp}
            loss, pred, summary, output, _ = session.run(
                [self.calculate_loss, self.pred, self.merged, self.output, train_op], feed_dict=feed)

            if train_writer is not None:
                train_writer.add_summary(
                    summary, num_epoch * total_steps + step)

            answers = a[step *
                        config.batch_size:(step + 1) * config.batch_size]
            questions = qp[step *
                           config.batch_size:(step + 1) * config.batch_size]

            if self.config.multi_label:
                # multi target
                correct = 0
                pred = pred.indices.tolist()
                for i in range(config.batch_size):
                    predicts = pred[i]
                    labels = answers[i]
                    labels = [idx for idx, i in enumerate(
                        labels) if int(i) == 1]
                    while len(predicts) > len(labels):
                        predicts.pop()
                    if set(predicts) == set(labels):
                        correct += 1
                    else:
                        Q = ''.join([self.idx2w.get(idx, '')
                                     for idx in questions[i].astype(np.int32).tolist()])
                        Q = Q.replace('unk', '')
                        labels.sort()
                        predicts.sort()
                        A = ','.join([self.idx2candid[a] for a in labels])
                        P = ','.join([self.idx2candid[p] for p in predicts])
                        error.append((Q, A, P))
                accuracy += correct / float(len(answers))

            else:
                accuracy += np.sum(pred == answers) / float(len(answers))

                for Q, A, P in zip(questions, answers, pred):
                    if A != P:
                        Q = ''.join([self.idx2w.get(idx, '')
                                     for idx in Q.astype(np.int32).tolist()])
                        Q = Q.replace('unk', '')
                        A = self.idx2candid[A]
                        P = self.idx2candid[P]
                        error.append((Q, A, P))

            total_loss.append(loss)
            if verbose and step % verbose == 0:
                sys.stdout.write('\r{} / {} : loss = {}'.format(
                    step, total_steps, np.mean(total_loss)))
                sys.stdout.flush()

        if verbose:
            sys.stdout.write('\r')

        return np.mean(total_loss), accuracy / float(total_steps), error

    def predict(self, session, inputs, input_lens, max_sen_len, questions, q_lens):
        feed = {
            self.question_placeholder: questions,
            self.input_placeholder: inputs,
            self.question_len_placeholder: q_lens,
            self.input_len_placeholder: input_lens,
            self.dropout_placeholder: self.config.dropout
        }
        pred = session.run([self.pred], feed_dict=feed)

        return pred

    def __init__(self, config):
        self.config = config
        self.variables_to_save = {}
        self.load_data(debug=False)
        self.add_placeholders()
        self.output = self.inference()
        self.pred = self.get_predictions(self.output)
        self.calculate_loss = self.add_loss_op(self.output)
        self.train_step = self.add_training_op(self.calculate_loss)
        self.merged = tf.summary.merge_all()


if __name__ == '__main__':
    config = Config()
    dmn = DMN_PLUS(config)
