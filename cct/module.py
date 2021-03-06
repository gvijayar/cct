"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import imp
import inspect
import logging
import os
import string

from cct.errors import CCTError
from pkg_resources import resource_string, resource_filename

logger = logging.getLogger('cct')


class ChangeRunner(object):
    change = None
    modules = None
    results = []

    def __init__(self, change):
        self.change = change
        try:
            directory = os.environ['CCT_MODULES_PATH']
        except KeyError:
            directory = os.path.join(os.path.dirname(__file__), 'modules')
            self.modules = Modules()
        self.modules.find_modules(directory)

    def run(self):
        for module in self.change.changes:
            if module.name in self.modules.modules.keys():
                module.instance = self.modules.modules[module.name]
                runner = ModuleRunner(module)
            else:
                raise CCTError("no such module %s" %module.name)
            try:
                runner.run()
                logger.info("module %s succesfully processed all steps" %module.name)
                self.results.append(module)
            except:
                logger.error("module %s failed processing steps" %module.name)
                self.results.append(module)
                raise

    def print_result_report(self):
        for module in self.results:
            print("Processed module: %s" %module.name)
            for operation in module.operations:
                print("  %-30s: %s" % (operation.command, operation.state))

class ModuleRunner(object):
    module = None
    state = "NotRun"

    def __init__(self, module):
        self.module = module
        self.state = "Processing"

    def  run(self):
        self.module.instance = self.module.instance(self.module.name, self.module.operations, self.module.environment)
        for operation in self.module.operations:
            self.module._process_environment(operation)
            # FIXME inject environment
            try:
                logger.debug("executing module %s operation %s with args %s" % (self.module.name, operation.command, operation.args))
                self.module.instance.run(operation)
                self.state = "Passed"
            except Exception as e:
                self.state = "Error"
                logger.error("module %s cannot execute %s with args %s" % (self.module.name, operation.command, operation.args))
                logger.debug(e, exc_info=True)
                raise e
        self.module.instance.teardown()
        self.state = "Passed"

class Change(object):
    name = None
    description = None
    environment = {}
    modules = {}

    def __init__(self, name, changes, description=None, environment=None):
        self.name = name
        self.description = description
        self.changes = changes
        self.environment = environment

class Module(object):
    name = None
    environment = {}
    operations = []
    instance = None
    state = "NotRun"

    def __init__(self, name, operations, environment={}):
        self.name = name
        self.operations = operations
        self.environment = environment
        # TODO: we need to do it properly
        self.logger = logger

    def _replace_variables(self, string):
        result = ""
        for token in string.split(" "):
            logger.debug("processing token %s", token)
            if token.startswith("$"):
                var_name = token[1:]
                # set value from environment
                if os.environ.get(var_name):
                    logger.info("Using host variable %s" %token)
                    token = os.environ[var_name]
                elif self.environment.get(var_name):
                    logger.info("Using yaml file variable %s" %token)
                    token = self.environment[var_name]
            result += token + " "
        return result

    def setup(self):
        pass

    def teardown(self):
        pass

    def _get_resource(self, module, resource):
        return resource_string(module, resource)

    def _get_resource_path(self, module, resource):
        return resource_filename(module, resource)

    def _process_environment(self, operation):
        if '$' in operation.command:
            operation.command = self._replace_variables(operation.command)
        for i in xrange(len(operation.args)):
            if '$' in operation.args[i]:
                operation.args[i] = self._replace_variables(operation.args[i])

    def run(self, operation):
        try:
            logger.debug("invoking command %s", operation.command)
            method = getattr(self, operation.command)
            method_params = inspect.getargspec(method)
            args = []
            kwargs = {}
            for arg in operation.args:
                if '=' in arg:
                    key, value = arg.split('=',1)
                    if key in method_params.args:
                        kwargs[key]=value
                    else:
                        args.append(arg.strip())
                else:
                    args.append(arg.strip())
            method(*args, **kwargs)
            logger.debug("operaton '%s' Passed" %operation.command)
            operation.state = "Passed"
        except:
            logger.error("%s is not supported by module", operation.command)
            operation.state = "Error"
            self.state = "Error"
            raise

class Operation(object):
    """
    Object representing single operation
    """
    command = None
    args = []
    state = "NotRun"

    def __init__(self, command, args):
        self.command = command
        self.args=[]
        if args:
            for arg in args:
                self.args.append(arg.rstrip())

class Modules(object):
    modules = {} # contains module name + its instance

    def find_modules(self, directory):
        """
        Finds all modules in the subdirs of directory
        """
        logger.debug("discovering modules in %s" %directory)
        for root, _, files in os.walk(directory):
            for candidate in files:
                if os.path.splitext(candidate)[1] == '.py':
                    logger.debug("inspecting %s" %root + "/" + candidate)
                    self.check_module(root + "/" + candidate)

    def check_module(self, candidate):
        module_name = "cct.module." + os.path.dirname(candidate).split('/')[-1]
        logger.debug("importing module %s to %s" % (os.path.abspath(candidate), module_name))
        module = imp.load_source(module_name, os.path.abspath(candidate))
        # Get all classes from our module
        for name, clazz in inspect.getmembers(module, inspect.isclass):
            # Check that class is from our namespace
            if module_name == clazz.__module__:
                # Instantiate class
                cls = getattr(module, name)
                if issubclass(cls, Module):
                    logger.info("found %s" %cls)
                    self.modules[module_name.split('.')[-1] + "." + cls.__name__] = cls

    def list(self):
        # HACK - enable CCT_MODULE_PATH variable override
        try:
            directory = os.environ['CCT_MODULES_PATH']
        except KeyError:
            directory = os.path.join(os.path.dirname(__file__), 'modules')
        self.find_modules(directory)
        print("available cct modules:")
        for module, _ in self.modules.iteritems():
            print("  %s" %module)

    def list_module_oper(self,name):
        try:
            directory = os.environ['CCT_MODULES_PATH']
        except KeyError:
            directory = os.path.join(os.path.dirname(__file__), 'modules')

        self.find_modules(directory)

        module = Module(name, None)
        if module.name in self.modules.keys():
            module.instance = self.modules[module.name]
        else:
            print("Module %s cannot be found!" %name)
            return
        print("Module %s contains commands: " % name)
        module.instance = module.instance(name, None)

        if getattr(module.instance, "setup").__doc__:
            print("  setup: %s " %getattr(module.instance, "setup").__doc__)

        for method in dir(module.instance):
            if callable(getattr(module.instance, method)):
                if method[0] in string.ascii_lowercase and method not in ['run1', 'setup', 'teardown']:
                    print("  %s: %s" %(method, getattr(module.instance, method).__doc__))

        if getattr(module.instance, "teardown").__doc__:
            print("  teardown: %s " %getattr(module.instance, "teardown").__doc__)
