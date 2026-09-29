import sqlite3
import os
import random
from datetime import datetime, timedelta

def seed_sample_database(db_path: str, scale: float = 1.0) -> None:
    """
    Creates and populates a realistic sample database for query optimization demos.
    scale: multiplier for dataset size (1.0 = ~25,000 orders, 10,000 employees).
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # Remove old database if exists to ensure clean reproducible state
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Create Schema Tables
    cursor.executescript("""
        CREATE TABLE Department (
            department_id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_name TEXT NOT NULL,
            location TEXT NOT NULL
        );

        CREATE TABLE Employee (
            employee_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            salary REAL NOT NULL,
            email TEXT NOT NULL,
            hire_date TEXT NOT NULL,
            job_title TEXT NOT NULL,
            FOREIGN KEY (department_id) REFERENCES Department(department_id)
        );

        CREATE TABLE Project (
            project_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            budget REAL NOT NULL,
            department_id INTEGER NOT NULL,
            FOREIGN KEY (department_id) REFERENCES Department(department_id)
        );

        CREATE TABLE EmployeeProject (
            employee_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            hours_allocated INTEGER NOT NULL,
            role TEXT NOT NULL,
            PRIMARY KEY (employee_id, project_id),
            FOREIGN KEY (employee_id) REFERENCES Employee(employee_id),
            FOREIGN KEY (project_id) REFERENCES Project(project_id)
        );

        CREATE TABLE Customer (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            city TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE Products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL
        );

        CREATE TABLE Orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES Customer(customer_id),
            FOREIGN KEY (product_id) REFERENCES Products(product_id)
        );
    """)

    random.seed(42)  # Deterministic seed for reproducible testing

    print(f"Seeding sample database at: {db_path}...")

    # 2. Seed Departments
    dept_names = ["Engineering", "Human Resources", "Sales", "Marketing", "Finance", "Legal", "Operations", "Research & Development", "Customer Support", "IT Infrastructure"]
    locations = ["New York", "San Francisco", "London", "Tokyo", "Berlin", "Austin", "Seattle", "Chicago", "Boston", "Toronto"]
    
    num_depts = int(50 * scale)
    departments = []
    for i in range(1, num_depts + 1):
        dname = f"{random.choice(dept_names)} {i}" if i > 10 else dept_names[i-1]
        loc = random.choice(locations)
        departments.append((i, dname, loc))
    
    cursor.executemany("INSERT INTO Department (department_id, department_name, location) VALUES (?, ?, ?)", departments)

    # 3. Seed Employees
    first_names = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda", "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"]
    titles = ["Software Engineer", "Senior Developer", "Data Analyst", "Project Manager", "Sales Representative", "HR Specialist", "Accountant", "QA Engineer", "DevOps Engineer", "Product Designer"]

    num_employees = int(10000 * scale)
    employees = []
    base_date = datetime(2018, 1, 1)

    for i in range(1, num_employees + 1):
        fname = random.choice(first_names)
        lname = random.choice(last_names)
        full_name = f"{fname} {lname}"
        dept_id = random.randint(1, num_depts)
        salary = round(random.uniform(45000, 160000), 2)
        email = f"{fname.lower()}.{lname.lower()}{i}@example.com"
        hire_date = (base_date + timedelta(days=random.randint(0, 2500))).strftime("%Y-%m-%d")
        title = random.choice(titles)
        employees.append((i, full_name, dept_id, salary, email, hire_date, title))

    cursor.executemany("INSERT INTO Employee (employee_id, name, department_id, salary, email, hire_date, job_title) VALUES (?, ?, ?, ?, ?, ?, ?)", employees)

    # 4. Seed Projects
    num_projects = int(200 * scale)
    projects = []
    project_prefix = ["Alpha", "Beta", "Gamma", "Delta", "Titan", "Apollo", "Phoenix", "Cyber", "Cloud", "Quantum"]
    project_suffix = ["Migration", "Portal", "Analytics", "Redesign", "Engine", "Mobile App", "Automation", "Security", "AI Integration", "Platform"]

    for i in range(1, num_projects + 1):
        pname = f"Project {random.choice(project_prefix)} {random.choice(project_suffix)} {i}"
        budget = round(random.uniform(10000, 500000), 2)
        dept_id = random.randint(1, num_depts)
        projects.append((i, pname, budget, dept_id))

    cursor.executemany("INSERT INTO Project (project_id, project_name, budget, department_id) VALUES (?, ?, ?, ?)", projects)

    # 5. Seed EmployeeProject
    num_ep = int(15000 * scale)
    ep_pairs = set()
    ep_records = []
    roles = ["Lead Developer", "Contributor", "QA Specialist", "Project Coordinator", "Reviewer"]

    while len(ep_records) < num_ep:
        emp_id = random.randint(1, num_employees)
        proj_id = random.randint(1, num_projects)
        if (emp_id, proj_id) not in ep_pairs:
            ep_pairs.add((emp_id, proj_id))
            hours = random.randint(5, 40)
            role = random.choice(roles)
            ep_records.append((emp_id, proj_id, hours, role))

    cursor.executemany("INSERT INTO EmployeeProject (employee_id, project_id, hours_allocated, role) VALUES (?, ?, ?, ?)", ep_records)

    # 6. Seed Customers & Products & Orders
    num_customers = int(2000 * scale)
    customers = []
    cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose"]
    
    for i in range(1, num_customers + 1):
        fname = random.choice(first_names)
        lname = random.choice(last_names)
        cname = f"{fname} {lname}"
        cemail = f"customer{i}@domain.com"
        city = random.choice(cities)
        created_at = (base_date + timedelta(days=random.randint(0, 2000))).strftime("%Y-%m-%d")
        customers.append((i, cname, cemail, city, created_at))

    cursor.executemany("INSERT INTO Customer (customer_id, name, email, city, created_at) VALUES (?, ?, ?, ?, ?)", customers)

    num_products = int(500 * scale)
    products = []
    categories = ["Electronics", "Office Supplies", "Furniture", "Software", "Hardware", "Services"]
    
    for i in range(1, num_products + 1):
        pname = f"Product Item #{i}"
        cat = random.choice(categories)
        price = round(random.uniform(10, 2000), 2)
        products.append((i, pname, cat, price))

    cursor.executemany("INSERT INTO Products (product_id, product_name, category, price) VALUES (?, ?, ?, ?)", products)

    num_orders = int(25000 * scale)
    orders = []
    statuses = ["COMPLETED", "PENDING", "SHIPPED", "CANCELLED", "PROCESSING"]
    
    for i in range(1, num_orders + 1):
        cid = random.randint(1, num_customers)
        pid = random.randint(1, num_products)
        odate = (base_date + timedelta(days=random.randint(0, 2600))).strftime("%Y-%m-%d")
        qty = random.randint(1, 20)
        unit_price = products[pid - 1][3]
        amt = round(qty * unit_price, 2)
        st = random.choice(statuses)
        orders.append((i, cid, pid, odate, qty, amt, st))

    cursor.executemany("INSERT INTO Orders (order_id, customer_id, product_id, order_date, quantity, amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)", orders)

    conn.commit()

    # Note: We intentionally do NOT create secondary indexes on Employee(department_id), Employee(salary), Orders(status), etc.
    # This ensures that standard search/filter queries trigger EXPLAIN QUERY PLAN SCAN TABLE so our optimizer can demonstrate
    # adding indexes and measuring the exact execution speedup!

    conn.close()
    print(f"Sample database successfully populated! Total records created: ~{num_employees + num_depts + num_projects + num_ep + num_customers + num_products + num_orders}.")

if __name__ == "__main__":
    db_file = os.path.join(os.path.dirname(__file__), "sample_database.db")
    seed_sample_database(db_file)
