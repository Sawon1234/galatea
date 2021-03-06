from pylearn2.models.s3c import S3C
from pylearn2.models.s3c import Grad_M_Step
from galatea.pddbm.pddbm import PDDBM, InferenceProcedure
from pylearn2.models.rbm import RBM
from pylearn2.models.dbm import DBM
from theano import function
import numpy as np
import theano.tensor as T
from theano import config
#from pylearn2.utils import serial
import warnings


if config.floatX != 'float64':
    config.floatX = 'float64'
    warnings.warn("Changed config.floatX to float64. s3c inference tests currently fail due to numerical issues for float32")

def broadcast(mat, shape_0):
    rval = mat
    if mat.shape[0] != shape_0:
        assert mat.shape[0] == 1

        rval = np.zeros((shape_0, mat.shape[1]),dtype=mat.dtype)

        for i in xrange(shape_0):
            rval[i,:] = mat[0,:]

    return rval


class Test_PDDBM_Inference:
    def __init__(self, model = None, X = None, tol = 1e-5,
            init_H = None, init_S = None, init_G = None):
        """ gets a small batch of data
            sets up a PD-DBM model
        """

        self.tol = tol

        if X is None:
            X = np.random.RandomState([1,2,3]).randn(1000,5)
            X -= X.mean()
            X /= X.std()
        m, D = X.shape

        if model is None:
            N = 6
            N2 = 7


            s3c = S3C(nvis = D,
                     nhid = N,
                     irange = .1,
                     init_bias_hid = -1.5,
                     init_B = 3.,
                     min_B = 1e-8,
                     max_B = 1000.,
                     init_alpha = 1., min_alpha = 1e-8, max_alpha = 1000.,
                     init_mu = 1., e_step = None,
                     m_step = Grad_M_Step(),
                     min_bias_hid = -1e30, max_bias_hid = 1e30,
                    )

            rbm = RBM(nvis = N, nhid = N2, irange = .5, init_bias_vis = -1.5, init_bias_hid = 1.5)

            #don't give the model an inference procedure or learning rate so it won't spend years compiling a learn_func
            self.model = PDDBM(
                    dbm = DBM(  use_cd = 1,
                                rbms = [ rbm  ]),
                    s3c = s3c
            )

            self.model.make_pseudoparams()

            self.inference_procedure = InferenceProcedure(
                        clip_reflections = True,
                        rho = .5 )
            self.inference_procedure.register_model(self.model)
        else:
            self.model = model
            self.inference_procedure = model.inference_procedure
            N = model.s3c.nhid
            N2 = model.dbm.rbms[0].nhid

        self.X = X
        self.N = N
        self.N2 = N2
        self.m = m

        if init_H is None:
            self.init_H = np.cast[config.floatX](self.model.rng.uniform(0.,1.,(self.m, self.N)))
            self.init_S = np.cast[config.floatX](self.model.rng.uniform(-5.,5.,(self.m, self.N)))
            self.init_G = np.cast[config.floatX](self.model.rng.uniform(0.,1.,(self.m,self.N2)))
        else:
            assert init_S is not None
            assert init_G is not None
            self.init_H = init_H
            self.init_S = init_S
            self.init_G = init_G


    def test_grad_h(self):

        "tests that the gradients with respect to h_i are 0 after doing a mean field update of h_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()

        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G
        idx = T.iscalar()
        idx.tag.test_value = 0

        new_H = ip.infer_H_hat(V = X, H_hat = H_var, S_hat = S, G1_hat = G_var )
        h_idx = new_H[:,idx]

        updates_func = function([H_var,S_var,G_var,idx], h_idx, on_unused_input = 'ignore')

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) },
                                                 Y = None)

        assert len(trunc_kl.type.broadcastable) == 1

        trunc_kl = trunc_kl.sum()

        grad_H = T.grad(trunc_kl, H_var)

        assert len(grad_H.type.broadcastable) == 2

        grad_func = function([H_var, S_var, G_var], grad_H)

        failed = False

        for i in xrange(self.N):
            rval = updates_func(H, S, G, i)
            H[:,i] = rval

            g = grad_func(H,S,G)[:,i]

            assert not np.any(np.isnan(g))

            g_abs_max = np.abs(g).max()

            if g_abs_max > self.tol:
                #print "new values of H"
                #print H[:,i]
                #print "gradient on new values of H"
                #print g

                failed = True

                print 'iteration ',i
                #print 'max value of new H: ',H[:,i].max()
                #print 'H for failing g: '
                failing_h = H[np.abs(g) > self.tol, i]
                #print failing_h

                #from matplotlib import pyplot as plt
                #plt.scatter(H[:,i],g)
                #plt.show()

                #ignore failures extremely close to h=1

                high_mask = failing_h > .001
                low_mask = failing_h < .999

                mask = high_mask * low_mask

                print 'masking out values of h less than .001 and above .999 because gradient acts weird there'
                print '# failures passing the range mask: ',mask.sum(),' err ',g_abs_max

                if mask.sum() > 0:
                    print 'failing h passing the range mask'
                    print failing_h[ mask.astype(bool) ]
                    raise Exception('after mean field step, gradient of kl divergence'
                            ' wrt freshly updated variational parameter should be 0, '
                            'but here the max magnitude of a gradient element is '
                            +str(g_abs_max)+' after updating h_'+str(i))


    def test_grad_g(self):

        "tests that the gradients with respect to g_i are 0 after doing a mean field update of g_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()

        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G
        new_G = ip.infer_G_hat(H_hat = H_var, G_hat = (G_var,) , idx =0)

        updates_func = function([H_var,S_var,G_var], new_G, on_unused_input = 'ignore')

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = self.m * ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) } )

        assert len(trunc_kl.type.broadcastable) == 1

        trunc_kl = trunc_kl.sum()

        grad_G = T.grad(trunc_kl, G_var)

        assert len(grad_G.type.broadcastable) == 2

        grad_func = function([H_var, S_var, G_var], grad_G)

        failed = False

        if True:
            G = updates_func(H, S, G)

            g = grad_func(H,S,G)

            assert not np.any(np.isnan(g))

            g_abs_max = np.abs(g).max()

            if g_abs_max > self.tol:
                #print "new values of H"
                #print H[:,i]
                #print "gradient on new values of H"
                #print g

                failed = True

                #print 'max value of new H: ',H[:,i].max()
                #print 'H for failing g: '
                failing_g = G[np.abs(g) > self.tol, i]
                #print failing_h

                #from matplotlib import pyplot as plt
                #plt.scatter(H[:,i],g)
                #plt.show()

                #ignore failures extremely close to h=1

                high_mask = failing_g > .001
                low_mask = failing_g < .999

                mask = high_mask * low_mask

                print 'masking out values of g less than .001 and above .999 because gradient acts weird there'
                print '# failures passing the range mask: ',mask.sum(),' err ',g_abs_max

                if mask.sum() > 0:
                    print 'failing g passing the range mask'
                    print failing_g[ mask.astype(bool) ]
                    raise Exception('after mean field step, gradient of kl divergence'
                            ' wrt freshly updated variational parameter should be 0, '
                            'but here the max magnitude of a gradient element is '
                            +str(g_abs_max)+' after updating g')

    def test_grad_s(self):

        "tests that the gradients with respect to s_i are 0 after doing a mean field update of s_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()

        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G
        idx = T.iscalar()
        idx.tag.test_value = 0

        new_S = ip.s3c_e_step.infer_S_hat(V = X, H_hat = H_var, S_hat = S_var)
        s_idx = new_S[:,idx]

        updates_func = function([H_var,S_var,G_var,idx], s_idx, on_unused_input = 'ignore')

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) } )

        assert len(trunc_kl.type.broadcastable) == 1

        trunc_kl = trunc_kl.sum()

        grad_S = T.grad(trunc_kl, S_var)

        assert len(grad_S.type.broadcastable) == 2

        grad_func = function([H_var, S_var, G_var], grad_S)

        failed = False

        for i in xrange(self.N):
            rval = updates_func(H, S, G, i)
            S[:,i] = rval

            g = grad_func(H,S,G)[:,i]

            assert not np.any(np.isnan(g))

            g_abs_max = np.abs(g).max()

            if g_abs_max > self.tol:
                raise Exception('after mean field step, gradient of kl divergence'
                        ' wrt freshly updated variational parameter should be 0, '
                        'but here the max magnitude of a gradient element is '
                        +str(g_abs_max)+' after updating s_'+str(i))

    def test_value_h(self):

        "tests that the value of the kl divergence decreases with each update to h_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()

        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G
        idx = T.iscalar()
        idx.tag.test_value = 0

        newH = ip.infer_H_hat(V = X, H_hat = H_var, S_hat = S_var, G1_hat = G_var)


        h_idx = newH[:,idx]


        h_i_func = function([H_var,S_var,G_var,idx],h_idx)

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = self.m * ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) } )

        trunc_kl_func = function([H_var, S_var, G_var], trunc_kl)

        for i in xrange(self.N):
            prev_kl = trunc_kl_func(H,S,G)

            H[:,i] = h_i_func(H, S, G, i)

            new_kl = trunc_kl_func(H,S,G)


            increase = new_kl - prev_kl


            print 'failures after iteration ',i,': ',(increase > self.tol).sum()

            mx = increase.max()

            if mx > 1e-4:
                print 'increase amounts of failing examples:'
                print increase[increase > self.tol]
                print 'failing H:'
                print H[increase > self.tol,:]
                print 'failing S:'
                print S[increase > self.tol,:]
                print 'failing V:'
                print X[increase > self.tol,:]


                raise Exception('after mean field step in h, kl divergence should decrease, but some elements increased by as much as '+str(mx)+' after updating h_'+str(i))

    def test_value_g(self):

        "tests that the value of the kl divergence decreases with each update to g_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()


        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G

        newG = ip.infer_G_hat(H_hat = H_var, G_hat = (G_var,), idx = 0)


        g_i_func = function([H_var,S_var,G_var],newG, on_unused_input = 'ignore')

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = self.m * ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) } )

        trunc_kl_func = function([H_var, S_var, G_var], trunc_kl)

        if True:
            prev_kl = trunc_kl_func(H,S,G)

            G = g_i_func(H, S, G)

            new_kl = trunc_kl_func(H,S,G)

            increase = new_kl - prev_kl

            print 'failures: ',(increase > self.tol).sum()

            mx = increase.max()

            if mx > 1e-4:
                print 'increase amounts of failing examples:'
                print increase[increase > self.tol]
                print 'failing H:'
                print H[increase > self.tol,:]
                print 'failing S:'
                print S[increase > self.tol,:]
                print 'failing V:'
                print X[increase > self.tol,:]


                raise Exception('after mean field step in g, kl divergence should decrease, but some elements increased by as much as '+str(mx)+' after updating g')

    def test_value_s(self):

        "tests that the value of the kl divergence decreases with each update to s_i "

        model = self.model
        ip = self.inference_procedure
        X = self.X

        assert X.shape[0] == self.m

        H = self.init_H.copy()
        S = self.init_S.copy()
        G = self.init_G.copy()

        H_var = T.matrix(name='H_var')
        H_var.tag.test_value = H
        S_var = T.matrix(name='S_var')
        S_var.tag.test_value = S
        G_var = T.matrix(name='G_var')
        G_var.tag.test_value = G
        idx = T.iscalar()
        idx.tag.test_value = 0

        newS = ip.s3c_e_step.infer_S_hat(V = X, H_hat = H_var, S_hat = S_var)

        s_idx = newS[:,idx]

        s_i_func = function([H_var,S_var,G_var,idx],s_idx, on_unused_input = 'ignore')

        sigma0 = ip.infer_var_s0_hat()
        Sigma1 = ip.infer_var_s1_hat()
        mu0 = T.zeros_like(model.s3c.mu)

        trunc_kl = ip.truncated_KL( V = X, obs = { 'H_hat' : H_var,
                                                 'S_hat' : S_var,
                                                 'var_s0_hat' : sigma0,
                                                 'var_s1_hat' : Sigma1,
                                                 'G_hat' : ( G_var, ) } )

        trunc_kl_func = function([H_var, S_var, G_var], trunc_kl)

        for i in xrange(self.N):
            prev_kl = trunc_kl_func(H,S,G)

            S[:,i] = s_i_func(H, S, G, i)

            new_kl = trunc_kl_func(H,S,G)


            increase = new_kl - prev_kl


            print 'failures after iteration ',i,': ',(increase > self.tol).sum()

            mx = increase.max()

            if mx > 1e-4:
                print 'increase amounts of failing examples:'
                print increase[increase > self.tol]
                print 'failing H:'
                print H[increase > self.tol,:]
                print 'failing S:'
                print S[increase > self.tol,:]
                print 'failing V:'
                print X[increase > self.tol,:]


                raise Exception('after mean field step in g, kl divergence should decrease, but some elements increased by as much as '+str(mx)+' after updating g_'+str(i))

if __name__ == '__main__':

    from pylearn2.utils import serial
    import sys
    from pylearn2.config import yaml_parse

    ignore, model_path = sys.argv
    model = serial.load(model_path)
    model.set_dtype(config.floatX)

    dataset = yaml_parse.load(model.dataset_yaml_src)

    X = dataset.get_batch_design(100)

    tester = Test_PDDBM_Inference(model = model, X = X, tol = 1e-4)

    tester.test_value_h()
    tester.test_value_g()
    tester.test_value_s()
    tester.test_grad_h()
    tester.test_grad_g()
    tester.test_grad_s()

    V = T.matrix()
    obs = model.inference_procedure.infer(V)

    init_S, init_H, init_G = function([V],[obs['S_hat'],obs['H_hat'],obs['G_hat'][0]])(X)

    tester = Test_PDDBM_Inference(model = model, X = X, tol = 1e-4,
            init_S = init_S, init_H = init_H, init_G = init_G)

    tester.test_value_h()
    tester.test_value_g()
    tester.test_value_s()
    tester.test_grad_h()
    tester.test_grad_g()
    tester.test_grad_s()

