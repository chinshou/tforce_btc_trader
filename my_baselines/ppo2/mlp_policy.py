from baselines.common.mpi_running_mean_std import RunningMeanStd
import numpy as np
import baselines.common.tf_util as U
import tensorflow as tf
import gym
from baselines.common.distributions import make_pdtype

class MlpPolicy(object):
    recurrent = True
    def __init__(self, name, *args, **kwargs):
        with tf.variable_scope(name):
            self._init(*args, **kwargs)
            self.scope = tf.get_variable_scope().name

    def lstm(self, x, size, name):
        cell = tf.nn.rnn_cell.LSTMCell(size, state_is_tuple=True)

        c_init = np.zeros((1, cell.state_size.c), np.float32)
        h_init = np.zeros((1, cell.state_size.h), np.float32)
        self.rnn_state_init = (c_init, h_init)

        c_in = U.get_placeholder(name="c_in", dtype=tf.float32, shape=[None, cell.state_size.c])
        h_in = U.get_placeholder(name="h_in", dtype=tf.float32, shape=[None, cell.state_size.h])
        self.rnn_state_in = (c_in, h_in)

        rnn_in = tf.expand_dims(x, [0])
        state_in = tf.contrib.rnn.LSTMStateTuple(c_in, h_in)

        lstm_outputs, lstm_state = tf.nn.dynamic_rnn(
            cell, rnn_in,
            initial_state=state_in,
            time_major=False)
        lstm_c, lstm_h = lstm_state

        self.rnn_state_out = (lstm_c[:1, :], lstm_h[:1, :])
        rnn_out = tf.reshape(lstm_outputs, [-1, size])
        return rnn_out

    def _init(self, ob_space, ac_space, hid_size, num_hid_layers, gaussian_fixed_var=True):
        assert isinstance(ob_space, gym.spaces.Box)

        self.pdtype = pdtype = make_pdtype(ac_space)
        sequence_length = None

        ob = U.get_placeholder(name="ob", dtype=tf.float32, shape=[sequence_length] + list(ob_space.shape))

        with tf.variable_scope("obfilter"):
            self.ob_rms = RunningMeanStd(shape=ob_space.shape)

        obz = tf.clip_by_value((ob - self.ob_rms.mean) / self.ob_rms.std, -5.0, 5.0)
        last_out = obz

        # LSTM
        last_out = self.lstm(last_out, hid_size, "vffc_lstm")
        self.rnn_last_state = self.rnn_state_init
        rnn_out = last_out

        for i in range(num_hid_layers):
            last_out = tf.nn.tanh(U.dense(last_out, hid_size, "vffc%i"%(i+1), weight_init=U.normc_initializer(1.0)))
        self.vpred = U.dense(last_out, 1, "vffinal", weight_init=U.normc_initializer(1.0))[:,0]

        last_out = obz
        for i in range(num_hid_layers):
            last_out = tf.nn.tanh(U.dense(last_out, hid_size, "polfc%i"%(i+1), weight_init=U.normc_initializer(1.0)))
        if gaussian_fixed_var and isinstance(ac_space, gym.spaces.Box):
            mean = U.dense(last_out, pdtype.param_shape()[0]//2, "polfinal", U.normc_initializer(0.01))
            logstd = tf.get_variable(name="logstd", shape=[1, pdtype.param_shape()[0]//2], initializer=tf.zeros_initializer())
            pdparam = U.concatenate([mean, mean * 0.0 + logstd], axis=1)
        else:
            pdparam = U.dense(last_out, pdtype.param_shape()[0], "polfinal", U.normc_initializer(0.01))

        self.pd = pdtype.pdfromflat(pdparam)

        self.state_in = []
        self.state_out = []

        stochastic = tf.placeholder(dtype=tf.bool, shape=())
        ac = U.switch(stochastic, self.pd.sample(), self.pd.mode())
        self._act = U.function(
            [stochastic, ob, self.rnn_state_in[0], self.rnn_state_in[1]],
            [ac, self.vpred, self.rnn_state_out],
        )

    def act(self, stochastic, ob):
        ac1, vpred1, self.rnn_last_state = self._act(stochastic, ob[None], self.rnn_last_state[0], self.rnn_last_state[1])
        return ac1[0], vpred1[0], self.rnn_last_state
    def get_variables(self):
        return tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, self.scope)
    def get_trainable_variables(self):
        return tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, self.scope)
    def get_initial_state(self):
        return []

